# Current repository state

## Verified camera probe feature handoff (2026-09-11)

E3 DEV TEST selects Camera-selected probe positioning, version 0.7.69,
exact code revision 850d176429659eb8b550f4d5213fc5c6b2f38f1d, from
`.codex-worktrees/focus-camera-probe-dev/dist/E3/E3.exe`. The required Windows
build script and native-library guard passed. All 157 unique packaged E3
source modules matched the isolated checkout; the EXE and adjacent build-info
match the permanent pointer. The normal launcher was not changed.

Fast Development CI 34662785230 passed on that exact code revision: Windows
Python 3.12 full suite 5,237 passed/25 skipped; POSIX transport/session recovery
493 passed; Ruff, dependency and compile checks passed. CI:
https://github.com/lukelave-boop/E3/actions/runs/34662785230

The handoff is `dist/focus-camera-probe-0.7.69/START_HERE.md`; artifact hashes and build
metadata are in adjacent `verification.json`. The Pi companion is
`e3-pi-laser-focus-170fa49a`, installed after 7602e2df; mainboard firmware stays
9518b83f. Offscreen widget/render tests were performed, not interactive frozen
GUI, live camera or hardware alignment tests. The first operator test selects a solid
camera target and checks the probe placement at clearance before probing.
Active max40 and saved X+3.302/Y+38.608 are preserved.
Physical gauge calibration, transfer acceptance and job integration remain
active work on this branch.

## Active: camera-selected probe positioning (2026-09-11)

The Surface / laser focus window now has Position probe, a live-image crosshair,
calibrated target preview, and a separate Move probe here action. Raw pixels
are corrected through the loaded lens model and active bed mapping. The typed
Pi operation positions the probe at verified clearance, accounting for saved
probe and configured laser-center offsets, and retains the same measured point
for the laser return. It does not automatically probe or lower Z. Bed-plane
selection does not correct raised surfaces. Frame/calibration/source/bounds,
Z/reference, session, STOP and confirmation checks reject invalid selection.

Operator installed the previous 7602e2df Pi companion; the supplied screenshot
shows live1920x1080 camera, saved X+3.302/Y+38.608, Z30 and active max40.
That is UI/live-feed evidence, not physical offset-transfer accuracy. The new
click-positioning code has focused simulated transport/mapping and offscreen
Qt acceptance/rejection tests. Frozen build, full CI and handoff verification
will be recorded separately. Physical click placement and gauge acceptance
remain unverified; this branch remains active for operator qualification.

## Verified live focus feature handoff (2026-09-11)

E3 DEV TEST selects Live focus view and probe alignment, version 0.7.67,
exact code revision 9bc42b1a305bd54d4ba2a8f6311070843d9634d5, from
`.codex-worktrees/focus-live-offset-dev/dist/E3/E3.exe`. The required Windows
build script and native-library guard passed. All 157 unique packaged E3
source modules matched the isolated checkout; the EXE and adjacent build-info
match the permanent pointer. The normal launcher was not changed.

Fast Development CI 34660818669 passed on that exact code revision: Windows
Python 3.12 full suite 5,125 passed/25 skipped; POSIX transport/session recovery
493 passed; Ruff, dependency and compile checks passed. CI:
https://github.com/lukelave-boop/E3/actions/runs/34660818669

The handoff is `dist/focus-live-0.7.67/START_HERE.md`; artifact hashes and build
metadata are in adjacent `verification.json`. The Pi companion is
`e3-pi-laser-focus-7602e2df`, installed after a43df3a1; mainboard firmware stays
9518b83f. Offscreen widget/render tests were performed, not interactive frozen
GUI, live camera or hardware alignment tests. The first operator XY check uses
saved X+3.302/Y+38.608 at selected clearance before probing. Active max40 stays.
Physical gauge calibration, transfer acceptance and job integration remain
active work on this branch.

## Active: live focus view and measured XY transfer (2026-09-11)

The focus window now includes an observational live camera pane,
frame age/stale/offline indication, step guidance, and explicit probe/laser
transfers at verified clearance. Probe XY values are unknown until saved;
operator supplied +3.302 mm right and +38.608 mm back relative to laser center.
Those CAD dimensions and directions are supplied evidence, not a physical XY
transfer test. A matching Pi companion is required; no new firmware flash is
needed. Camera pixels grant no motion coordinates or raised-surface correction.

Operator evidence since the preceding handoff: the 9518b83f application was
uploaded and committed over USB after an explicit M997/HOLD/identity handoff,
then booted. M115 reported Sep 10 19:10:34, surface-height V2, Z80 and emergency
parser capabilities, MCU423/256KiB. The desktop showed active max40 and confirmed
firmware ceiling80. Operator then ran Home/park and Reference border; screenshot
showed Z30.000 with max40. Gauge teaching and the new offset transfers remain
physically unverified. Earlier USB-pending notes below are historical.

Verification: 298 combined focused desktop/camera/focus/RPC/Z/service tests passed
on Windows; repository Ruff and compileall passed. The window was rendered and
visually reviewed offscreen with a labelled simulated bed image. No live camera,
interactive frozen build or hardware transfer test has been performed. The new
Pi kit is e3-pi-laser-focus-7602e2df. Its installer was tested against a local
reconstruction of the installed a43df3a1 files: 4 updated, 7 current, all hashes
matched, reapplication idempotent, config/max40/cooling/calibration preserved.
Only the Linux service-inactive check was stubbed for that offline installation.

## Verified focus feature handoff (2026-09-10)

E3 DEV TEST now selects Gauge focus and raised surfaces, version 0.7.65,
exact code revision e69550b79ccd3e7a24e559dc2fe6185272143b19, from
`.codex-worktrees/laser-focus-dev/dist/E3/E3.exe`. The frozen executable and
adjacent build-info match; all 312 packaged E3 modules matched that checkout.
The required Windows build script and native-library guard passed. The normal
launcher was not changed. Offscreen UI rendering/tests were performed; the
frozen application was not interactively connected to hardware.

Fast Development CI 34550282514 passed on that exact revision: Windows Python
3.12 full suite 5,082 passed/25 skipped; POSIX transport/session recovery 493
passed; Ruff, dependency and compile checks passed. CI:
https://github.com/lukelave-boop/E3/actions/runs/34550282514

The actual a43df3a1 Pi installer was also applied to a local reconstruction of
the operator-installed a1c79819 baseline: all 11 target hashes matched,
configuration/Z-limit/cooling files were preserved, and reapplication was
idempotent. Only the service-state check was stubbed; no Pi was accessed.
The staged handoff is `dist/laser-focus-0.7.65/START_HERE.md`; artifact hashes
and exact build metadata are in its adjacent `verification.json`. Application
USB update, new native V2 probing, teaching and physical gauge acceptance are
still operator work. No hardware qualification or automatic job focus is
claimed. This development branch remains active for that qualification and
subsequent job integration.

## Active: gauge-taught laser focus and raised-surface setup (2026-09-10)

A new explicit laser-off focus workflow teaches the operator's 7 mm gauge
setting from a measured surface and acknowledged Ender Z. The 5 and 3 mm
selections lower the target by 2 and 4 mm. Surface elevation is not called
material thickness. A machine-bound, validated calibration sidecar survives
restart; border references, measured surfaces and movement previews do not.
New G39 C/H V2 probing retains native fast/slow handling and constrains contact
by selected clearance, actual probe geometry and the firmware Z80 ceiling.
The old parameterless G39 V1 path remains available.

The desktop provides native reference/measure, small teaching jogs, save,
preview, move and return-to-clearance from Machine and Machine Setup. Ordinary
XY/Home/arming/job starts are blocked while the focus path requires clearance.
This is calibration and position validation, not automatic job focus, per-layer
focus, or camera height correction. Actual gauge fit, accuracy within the
operator's 0.6 mm tolerance, raised-surface behavior and recovery require new
physical validation. Earlier operator fan/probe/Z observations remain evidence
for their exact older builds only. No hardware, service or firmware upload was
operated by the assistant. The Pi companion is e3-pi-laser-focus-a43df3a1 and the compact F401 V2
firmware candidate is e3-mainboard-f401-usb-9518b83f. See docs/LASER_FOCUS.md.

Verification: 114 combined new focus/backend/RPC/installer/AppContext/Qt
tests passed; 138 desktop focus/Z/setup tests passed. Existing backend
regression groups passed (271 service/RPC and 383 probe/session/job/desktop
tests), as did the 128-test installation/cooling/Z group. These groups
overlap. Repository Ruff and compileall passed. The offscreen dialog was
rendered and visually checked; no interactive or physical controller test
was run. Firmware: 111 focused tests, compiled ARM probing in three
configurations, planner/output/M997 audits, and three pristine source
profile reproductions passed. The Windows frozen build and CI results are
recorded in the subsequent handoff; this branch remains experimental until
operator validation and job-integration work are complete.

## Active: natural numeric editing across desktop controls (2026-09-10)

The operator reported blocked deletion/replacement and decimal entry across
E3, including the new Z maximum field. Shared numeric controls now permit
incomplete drafts and defer valueChanged until commit. All desktop numeric
spinboxes and the stock-margin dialog use this behavior; explicit measurement
units and canonical speed semantics remain. Unrounded final values must pass
existing bounds before precision rounding. Invalid drafts restore the prior
value without publishing a clamped replacement. Z maximum live polling preserves
pending text and Apply validates the draft; a new controller session discards
old edits. Browser scalar submission rejects empty/non-finite input.

Verification: 168 focused Windows offscreen Qt/unit/browser checks and 10
full-window action/queue checks passed, including actual keyboard, focus and
mouse events. An additional 243 desktop migration checks passed. Repository Ruff and compileall passed. Local Python is 3.14.4.
Full Windows Python 3.12 CI passed and the frozen feature EXE is selected. No live GUI, camera, serial or motion tests were run
for this change. No Pi or firmware update is required. See docs/NUMERIC_INPUT.md.

Operator context: the preceding e3-pi-z-controls-a1c79819 companion applied
successfully on the Pi, border referencing returned Z20 and the operator
reported completion of the initial small Z-jog check. Firmware capability
E3_Z_LIMIT_80_V1 remains unconfirmed on the installed compact build. The numeric
editing defect prevented the maximum-setting UI test from completing.

Toolbar mirroring, aspect lock, stock layout and template nudges commit the
focused number before acting. Save/Generate and layer changes also commit
project numeric edits and flush only the deferred layer data queue before
capturing a document snapshot; tree rebuilds remain outside native item signals.

Fast CI for 11db1bc5b980ebac6294ddca73f63c3e0dd49687 passed: Windows
Python 3.12 full desktop suite 4964 passed/25 skipped, focused POSIX suite
493 passed, Ruff, dependency and bytecode validation passed. The additional
local layer-panel/Z/job-preparation regression group passed 155 checks.
CI: https://github.com/lukelave-boop/E3/actions/runs/34547013092

Frozen E3 DEV TEST 0.7.63 was produced with packaging/build_windows.ps1
and E3_BUILD_VERSION=0.7.63 from .codex-worktrees/numeric-entry-dev at that
exact revision. Build metadata matches; all 154 packaged E3 Python modules
were traced to the isolated checkout and the bundled-library guard passed.
EXE SHA-256: 2dea29b1d66a3540e01cfc76df1ea91545b7f53b89b7d4c17297c5e264b6f6e2.
The permanent current-feature.json was selected with the validated helper and
read back successfully. A mock-status Z panel was rendered offscreen and
visually inspected with a pending40 draft and active80 maximum. This is not
operator or hardware verification. The ordinary E3 launcher is unchanged.

## Active: desktop Ender Z controls and saved maximum (2026-09-10)

The Machine tab now provides independent Ender Z-/Z+ controls, steps 0.1/1/5 mm,
and an adjacent controller-reported Z height. Explicit asynchronous readbacks
run while connected and idle; stale/unknown positions do not authorize motion.
STOP, controller session changes and foreground operations invalidate or suppress
old requests. A visible stowed-probe/path-clear checkbox gates one-click jogging.
No automatic homing or raw primary-controller Z commands were added.

The active maximum is shown beside an editable 20..80 mm setting. The Pi owns
fresh read/compute/jog under the existing shared controller lock, validates
absolute and relative targets, and saves its ceiling atomically in a schema-1
configuration-adjacent sidecar bound to the Ender port. It refuses invalid saved
limits instead of silently restoring Z80. The configured ceiling also bounds
normal service probing clearance and initial native lifts; G39's fixed range
is unchanged. Remote desktop requires pi-mainboard-z-v1, and reports whether
firmware advertises E3_Z_LIMIT_80_V1. The separate 934ef4b3 firmware kit
provides the fixed Z80 ceiling; connection status reports whether it is present.

Verification: 210 focused Windows/offscreen desktop checks passed, including
real worker/timer responsiveness, Machine Setup serialization, STOP and session
cancellation; 102 backend/RPC/node/installer checks passed, including disk-stall
STOP handling and persistent limits; 74 app/firmware-package/installer checks
passed. Repository Ruff and compileall passed. The Pi companion
`e3-pi-z-controls-a1c79819` applied successfully to a disposable reconstruction
of the operator's known Pi source, which then imported successfully. A rendered
400 px Machine panel was visually inspected with mock status. Local Python is
3.14.4; Windows Python 3.12 full-suite and focused POSIX checks run in Fast CI.

Fast CI for 4b209f0164f0d6522dbd4e321897c83e358ef7b6 passed: Windows
Python 3.12 full desktop suite 4861 passed/25 skipped; focused POSIX suite 493
passed; Ruff, dependency and bytecode checks passed. Mainboard SD and compact
F401 firmware workflows also passed for that revision.

Frozen Windows feature 0.7.61 was produced with packaging/build_windows.ps1
from isolated checkout .codex-worktrees/z-controls-dev at that exact revision.
The bundled-library guard passed; build metadata was stamped with the documented
E3_BUILD_VERSION override after the sanitized build PATH lacked Git. The direct
feature EXE is dist/E3/E3.exe within that checkout; SHA-256
fe29fdb0a979628a27f459bf330ac051158b40723a7d53f8ae3001751ea5805f.
The permanent E3 DEV TEST current-feature.json was updated with the validated
packaging helper and read back successfully. This handoff selects the isolated
EXE. No ordinary installer was retained or installed.

No live GUI, camera, serial, motion, real Pi installation or firmware upload
was performed. The Pi companion and new desktop workflow require operator
verification. The normal launcher was not modified. See docs/MAINBOARD_Z_CONTROLS.md.

## Compact Z endpoint and Pi CPU cooling (2026-09-10)

Operator reports installed compact 08beaf0d firmware (Marlin 2.0.8.24F4,
Sep 10 2026 10:08:40, MCU:423 FLASH_KIB:256), Pi 0.7.54 with endpoint patch
393a2d2c. Native homing ends Z0; the corrected host verifies that endpoint
before its final Z20 lift. Operator observed full homing/clearance and manual
Z21/back-to-Z20. FAN1 is Pi cooler and FAN2 air assist, each physically on/off
verified; pin deploy/stow observed. The 7 mm sample measured 6.984, 6.983,
6.984 mm at one XY position with -0.001 border baseline and configured 1.5 mm
support offset. Reported 0.001 mm spread is not absolute accuracy calibration.

Added opt-in Pi E3_CPU_COOLING=1: 45 C on, 40 C off, five-second polling,
shared MachineService FAN1-only authority, STOP/session pause and no FAN2,
primary laser or motion command. Kit dist/e3-pi-cooling-79cbe077 validates
known previous source hashes and retains backups; existing probe/startup
patches are preserved. 125 focused Windows tests plus Ruff/compileall passed.
Exact original node/service LF sources pass local patch/backup verification.
Windows/Linux fast CI is pending. Automatic thermal switching and installed
board USB uploads remain physically untested. The earlier service shutdown
timeout is not fixed by these changes. See docs/PI_CPU_COOLING.md for behavior,
installation and caliper-based next calibration checks. No main merge while
physical qualification and CI are pending.

## Active: compact F401 installer with initial USB updates (2026-09-10)

The restored working machine now boots stock 2.0.8.26F4 and responds to Pi M115
at 115200 (operator report 09:27:25 UTC below). The installed chip capacity is
still unknown. A separate compact candidate corrects two demonstrated old-build
limitations: the previous 214,620-byte installer crossed the 256 KiB boundary,
and its 512 KiB-only guard could become silently unresponsive on a mismatch;
the original host also lacked native Marlin-to-updater entry. These findings
are not proof of which limitation caused the installed machine's failure.

The new [compact kit](firmware/marlin_mainboard_compact/README.md) is
`dist/e3-mainboard-f401-usb-08beaf0d.zip`; its SD image is 147,188 bytes,
SHA256 `08beaf0d9ae2758dc6382f3de77ad5ecfdef49d6622b48bd44b27c08e0c53d2e`.
The 81,140-byte application uses 6,740 bytes of static RAM. It accepts only the
F401 ID/capacity pairs 0x423/256 KiB and 0x433/512 KiB, uses 64 KiB RAM and a
fixed 256 KiB flash layout, and leaves the first 64 KiB factory loader outside
all installer/update writes. Updater 0.3.0 occupies sector 4; application/header
occupy sector 5 at 0x08020000, vectors at 0x08020200, ending 0x08033EF4. Native
M115 reports actual MCU ID and flash size. Mismatch diagnostics remain queryable.

This headless profile retains FAN1/FAN2, native CR Touch/Z/G39, emergency parsing
and idle-only M997 USB maintenance entry. It removes stock touchscreen/menu,
SD job reading/resume, heaters, extrusion, filament handling and arcs. A splash
screen is therefore not evidence of failed startup; USB capabilities establish
application identity. EXTRUDERS=0 planner/readback assumptions were corrected.
EEPROM schema E31 rejects stock settings and loads defaults without automatic
settings writes. Physical direction, current settings, probe behavior and offsets
require attached-mechanism qualification before use.

The first SD installation includes the USB updater. The target-specific host
validates image/identity, enters M997 explicitly, holds and rechecks the updater,
then verifies and commits without automatic boot. Ordinary serial traffic does
not prevent its five-second auto-boot. The Pi companion installer accepts only
known startup-reader hashes, backs up and atomically changes that one module
while the service is inactive. No runtime configuration or dependency changes.
The kit includes exact firmware source, ELFs, manifest and operator-proven stock
recovery. Existing F401 BENCH/full-size and F103 artifacts remain separate.

Offline verification includes compiled updater recovery, 58 platform cases for
both silicon pairs/flash bounds, 457 compiled mismatch-diagnostic checks, three
native G39 configurations, compiled output-off/M115 checks, 64 M997 admission
cases and two actual Cortex-M4 stack-mode handoffs. Nine actual-ELF planner
cases exercise XYZ/E-ignored/cleaning/acceleration paths with simulated edges
and memory canaries; the physical stepper ISR is not exercised. Focused Windows
Python tests: 251 passed, one symlink-privilege skip. Repository Ruff and
compileall passed. A fresh source clone
reproduces the pinned patched files. Eleven real-Git preparation cases cover
LF/CRLF patch transport and source preservation; the CI-style Windows checkout
now prepares the same reviewed source. The dedicated CI workflow adds Windows
firmware rebuilding and Linux startup/support tests.

The selected operator kit is the exact artifact from passing Windows/Pi
[compact firmware CI](https://github.com/lukelave-boop/E3/actions/runs/34464261129),
source revision d1fdd23735965a3d4d53675ce8e7dcb4ddcdbf50: Windows clean build,
compiled native audits and 463 host/package tests passed; Linux startup/support
122 tests passed. Downloaded archive entries, source hashes, application image,
SD placement and stock recovery checksums were independently verified. The
locally built 505a4551 kit remains a development artifact; use 08beaf0d for the
operator test. No code was merged into main while physical acceptance is pending.

[Fast Development CI](https://github.com/lukelave-boop/E3/actions/runs/34464261305)
also passed at d1fdd23: Windows Python 3.12 desktop suite 4,677 passed / 25
platform skips; POSIX serial/session recovery 373 passed; Ruff and dependency/
bytecode checks passed. These are automated tests, not interactive GUI, camera,
controller or laser validation. The earlier clean-Windows patch and optional
ELF-import CI failures were corrected and covered by focused regression tests.

Implemented and tested offline, not physically accepted: this exact installer,
normal cold start with USB/webcam attached, USB application updates on the working
board, real fan/Z/probe behavior and measurement accuracy. Historical spare
BENCH recovery does not qualify this native application. No assistant hardware
operation was performed; the operator's Pi service remains stopped after the
stock diagnostic. Prior entries below record earlier stages.

## Active: connected-startup correction, updater 0.2.0 (2026-09-09)

The operator authorized fixing normal connected power-up. Updater 0.2.0 keeps
its five-second automatic boot deadline despite ordinary traffic, INFO/M115,
partial/oversized lines and UART errors. Only exact HOLD or a validated BEGIN
holds maintenance; invalid/incomplete images still remain in recovery. Flash
ownership, commit-last validation and physical output initialization are unchanged.
Replacing updater 0.1.0 requires the new combined SD installer, not a USB
application upload. Earlier accepted artifacts remain intact.

The Pi's existing Creality owner now performs a bounded read-only M115 readiness
handshake before its acknowledged fan-OFF exchange: up to 15 queries within
45 seconds, plus existing settle/synchronization bounds. Marlin identity and
OK are required; stale input is synchronized before OFF. Unknown firmware,
persistent updater, silence and transport faults fail closed. No runtime command,
reset, BOOT, HOLD, flash, probe or motion is replayed by this handshake. This is
startup readiness, not repair of a missing/faulted USB link or automatic job
resumption. The prior zero-response fault remains causally unconfirmed.

Local Windows verification: 479 focused Python tests pass, repository Ruff and
compileall pass, compiled Cortex-M4 core startup/recovery tests pass, production
flash-wrapper tests pass 27 cases, and bench application MMIO tests pass 23.
Native probing tests pass all three configurations; the mainboard build and
startup/kill audits pass. The application.e3fw is byte-identical to the previous
7f45686b package; only the updater changes in the combined firmware image.

Prepared SD package: dist/e3-mainboard-v1-6991d999, installer
SD_CARD/STM32F4_UPDATE/e3main_6991d999.bin, 214620 bytes, SHA-256
6991d999ed14fb82d43087bdca78b08a58d2b5f013b111c108fe8b0fb8e2f2f4.
Updater padded-region SHA-256:
5229f49d7477c62979ea51b0b3c6d8b38e23d7f14dd2ab1192620c0836278254.
STARTUP.md defines operator connected-power-up and recovery acceptance;
SOURCE_REVISION.txt pins the companion Pi source. No hardware, serial, service,
webcam or Pi operation was performed. Physical acceptance remains pending.

CI passed on implementation 5f52e403487ad286ff8de06df8840b7847fa5a38:
- Mainboard SD Firmware: https://github.com/lukelave-boop/E3/actions/runs/34351697836
  rebuilt/audited firmware, passed 277 Windows host/package tests and 51 Linux
  startup/owner tests, plus compiled updater/platform/native probing checks.
- Fast Development CI: https://github.com/lukelave-boop/E3/actions/runs/34351697711
  passed the full Windows Python 3.12 desktop suite (4491 passed, 25 skipped),
  POSIX serial/recovery tests, dependency/bytecode validation and repository Ruff.

The final local package's SOURCE_REVISION.txt and HOST_SOURCE build information
both pin 5f52e40; all packaged file hashes match its manifest. CI generated
05408fd0e4c30f8de2932fba7a4aa00a7c61c0a20f019c7915231a06acfc67a8
(214516 bytes): its Marlin embeds different absolute compiler paths and build
date/time strings. The updater checksum is identical. The downloaded CI ELF
also passed startup/layout/kill audits locally. The selected operator package
remains 6991d999 to retain the previous application's exact bytes. This is an
experimental physical-test handoff, not a main merge or production qualification.
Existing uncommitted USB-capture investigation notes are preserved separately.

## Active: SD-installable FAN1/FAN2, probe and Z mainboard firmware

The user requested a complete mainboard SD file ready for physical validation.
`firmware/marlin_mainboard/` now supplies it on `codex/marlin-material-height`,
preserving the earlier material patch, auxiliary updater, BENCH builds and all
recovery artifacts. No working-machine, Pi, webcam or service operation was
performed. No new Windows EXE was built or selected.

Final-product startup requirement, explicitly confirmed by the operator on
2026-09-09: Ender USB and the webcam must remain connected during ordinary
power-up. Manual unplugging, timed reconnection and hardware-service restarts
are diagnostic workarounds, not acceptable product operation. The retained
0.1.0 updater currently cancels automatic application boot on any received byte
during its five-second window, including ordinary host traffic. This is a
confirmed integration weakness, not yet the proven cause of this board's
zero-response fault. Production integration must separate deliberate update
entry from normal traffic and establish/recover controller readiness without
operator cable timing, while preserving recovery, output-off behavior, shared
serial ownership and the webcam. The proposed 30-second isolated boot is only
a diagnostic margin for updater plus native Marlin initialization.

Final package: `dist/e3-mainboard-v1-7f45686b`; install
`SD_CARD/STM32F4_UPDATE/e3main_7f45686b.bin` (214620 bytes), SHA-256
`7f45686bb003b6ecd83e628461b331b87c69aa382cfea4d1faa13f15704ac950`.
This is a complete initial SD installer at 0x08010000 for the identified
STM32F401RET6 / CR4NS200141C13 family: exact accepted updater 0.1.0 plus
Marlin at 0x08020200. It does not contain factory-loader bytes. The package
includes the official F401 2.0.8.26 stock rollback image, whose SHA is
`9a81f7564d62b2b131dcc96f0bf47725e3ee78c3b68b198bb54680f431e02710`,
corresponding firmware source, INSTALL.md and VALIDATE.md.

Real firmware support: independent FAN1/PC0 via M106 P1, FAN2/PA0 via M106 P0
(unindexed M106 stays FAN2), native probe deployment/stow, native Z homing and
movement, and the inherited bounded fast/slow G39 material cycle. FAN1's
automatic hotend ownership is disabled. M123 reports commanded fan PWM and Z
trust; M115 advertises exact E3_MAINBOARD_V1 and E3_MATERIAL_HEIGHT_V1 capabilities.
Emergency parsing is enabled. Native kill explicitly clears both PWM settings
and both fan pins; compiled Cortex-M4 execution with fake MMIO verified this.

Operator mainboard installation attempt, 2026-09-07: the user reported powering
the Ender with the supplied SD card, waiting three minutes, removing the card
and powering on again. The subsequent operator-run Inspect returned Pi build
0.7.31/a89995ba, boot e9248610-3d37-426f-baa3-07eb1b8daa96, controller session
generation 2, READY_HOME_REQUIRED and controller.rejected: "Secondary Creality
controller session failed: Serial connection closed unexpectedly". No firmware
identity was returned, so installation on the working board remains unconfirmed.
A stale connection after the power/USB cycle is a possible explanation; the
reply does not establish when the connection closed or its cause. At that point
the Pi still needed the packaged companion software update and a new
operator-established controller session. A read-only SSH log retrieval
could not authenticate; no controller commands or service operations were sent.

After the operator update/restart, mainboard status reported Pi 0.7.51/a454470b,
boot 43b74882-d6e3-4aeb-8888-9d87f15d03ba, primary session generation 1 and
READY_HOME_REQUIRED, but rejected because the shared Creality connection was
not initialized. An assistant read of cached machine.status (no controller
commands) retrieved the actual secondary fault: M106 S0 timed out, receiving
zero lines, on /dev/serial/by-id/usb-1a86_USB_Serial-if00-port0 at 115200 baud.
The secondary is configured but not ready; primary connection success does not
establish secondary readiness. The firmware identity remains unconfirmed.
Operator service/kernel logs are the next diagnostic evidence; no homing,
reflash or assistant hardware-service operation was initiated.

The operator's subsequent journal capture shows CH340 1a86:7523 on USB 1-1.2,
using dwc2, disconnected/re-enumerated at 16:22, 16:24 and 16:27-16:29. The
last attachment was ttyUSB0 at 16:29:54; no later kernel disconnect appears
through the capture at 16:36:27. Two -32 receive errors occurred immediately
before the 16:22 disconnect; they do not establish the cause or prove a new
spontaneous USB failure during these operator power/USB operations. The service
tail contains only machine.status polling from PID 69679, so it omits the
startup failure. USB enumeration alone does not confirm Marlin execution or
working serial transfers. The existing by-id configuration avoids dependence
on the observed ttyUSB0/ttyUSB1 renumbering. No webcam or dwc2 change is proposed.

On 2026-09-09 the operator reported a complete power-off and power-on of the
system. A read of cached machine.status returned a new Pi service boot ID,
34a7db96-66a7-42c2-a807-c071e6221cdf, still 0.7.51/a454470b. The primary was
disconnected; the configured secondary remained not ready with the same M106 S0
timeout and zero response lines at 115200 baud. Thus the failure recurred in a
new service boot, rather than being only the prior service's retained error.
Working-board firmware identity is still unconfirmed. The next proposed
operator test boots the Ender with USB unplugged, allows 30 seconds for startup,
then reconnects USB and restarts the Pi service before connecting in E3 and
requesting mainboard status. Startup-order involvement remains unproven. No
controller command, service restart, firmware change or webcam operation was
performed by the assistant during this check.

The operator completed that controlled startup and service restart. On Pi boot
01f08aa7-0c57-491c-85f6-2d1fcf33aa2e, mainboard status again rejected the
uninitialized secondary. An assistant cached-status read confirmed the same
M106 S0 timeout with zero received lines. The proposed boot-order workaround
did not restore communication in this test; its failure does not identify the
firmware currently running on the working board.

`scripts/inspect_ender_usb.py` and `docs/ENDER_USB_IDENTITY.md` now provide an
operator-run direct Windows identity diagnostic. It selects only one newly
attached CH340 (excluding existing adapters/spare), captures 35 seconds of
startup reception, sends M115 once and INFO only after ERR UNSUPPORTED. It
does not send reset/update/boot, motion, probe, fan or heater commands or touch
Pi/webcam services. It is a maintenance diagnostic, not a normal control path.
Sixteen Windows fake-serial tests, focused Ruff and compileall pass; no real
working-board execution has been performed by the assistant. The existing
firmware packages and Pi runtime are unchanged. Direct Windows identity is the
next operator evidence before any firmware correction or further probing.

The companion machine.mainboard RPC and mainboard_control CLI route independent
fan percentages and bounded manual Z through MachineService and the existing
shared Pi owner. Z requires trusted Z, a stowed-probe confirmation, 20–80 mm
target and <=5 mm per command. Manual Z invalidates material references. The
service retains idle/disarmed/hardware/motion/session/STOP guards and RPC replay
protection. Both fans participate in lifecycle cleanup, including idle manual
control without an enabled job Air Assist mapping. The application manual-command
allowlist and existing desktop measurement suspension are unchanged. The native
reference/measure and pin CLI operations remain available; new fan/Z controls
require this companion software revision on the Pi. No Pi update was initiated.

Spare physical evidence, 2026-09-07: positively identified BENCH 0.2.0 on
COM6/CH340 (1A86:7523, location 1-1), then uploaded the exact final application
from the SD package through the retained updater. It verified/committed and
booted with both capabilities and EMERGENCY_PARSER:1. FAN1/FAN2 command readbacks
passed independent 0/64/128/192/255 combinations. M280 deploy/stow reported servo
angles 10/90. A native 1 mm Z command reported 400 steps, then returned to Z0
and zero steps. Unhomed G39 rejected with PRECONDITION; M123 arguments rejected.
M997 returned to the updater, HOLD succeeded, and reboot returned to the same
Marlin image with unhomed rejection intact. Native automatic boot after M997
also passed after a 30-second startup interval, without HOLD or BOOT. An
earlier query at seven seconds timed out during native initialization; source
includes a ten-second display progress animation. Installation now specifies
60 seconds for the first SD boot and 30 seconds before reconnecting USB. **The spare now runs this final
mainboard firmware**, superseding the earlier BENCH-restored state below. Fans
were left commanded OFF, servo stowed, Z0. This verifies firmware command paths
on a real bare MCU; it does not measure connector voltage, attached-fan behavior,
probe movement, mechanical Z distance or contact accuracy. Full-machine SD
installation and those attached-load checks are the next operator physical
validation, using the included procedure. The webcam remains part of the system.

Verification: 200 focused existing/new host tests passed; five packaging tests
passed after correcting their synthetic vector fixture. A final 44-test new
control/RPC/package group passed, including idle STOP/disarm cleanup. All three
native probing configurations, real startup/VTOR audit, real kill GPIO audit,
repository Ruff and compileall passed. The independent final checks passed for
implementation a347eb1:
- Mainboard SD Firmware: https://github.com/lukelave-boop/E3/actions/runs/34162116137
  rebuilt the pinned firmware/updater, passed native probing/startup/fan-kill
  audits, assembled the complete SD/stock recovery package and passed 139 host tests.
- Fast Development CI: https://github.com/lukelave-boop/E3/actions/runs/34162116198
  passed 4452 Windows Python 3.12 tests (25 skipped), plus Ruff, dependencies,
  bytecode and POSIX controller-session coverage.
- Compatibility CI: https://github.com/lukelave-boop/E3/actions/runs/34162117861
  passed both Windows Python 3.10 core and Python 3.12 desktop jobs, Ruff and
  POSIX controller-session coverage.

The later installation-timing/source-metadata edits do not change the tested
SD binary or host runtime. A fresh pinned checkout reproduced the exact patched
source hashes. Final bundle checks verified every file checksum, its SD/ZIP byte
identity, official stock recovery and matching companion source; archive installs
also carry explicit build identity. The package is ready for operator SD transfer
and physical validation. Attached-load qualification remains the operator's next
step; all software/build/spare validation in this task is complete.

## Active: bounded native Marlin material-height prototype

Implemented on `codex/marlin-material-height`, starting from `eb6f1d4`, which
already includes material-height development through `d69d916`. Existing
auxiliary firmware, both recovery packages, worktrees and temporary files are
preserved. Main remains separate; this is an experimental branch, not a
production-ready machine integration. No new Windows EXE was built or selected.

`firmware/marlin_material/` pins Creality `s1_pro_plus` source at
`7fffa9270ff8fb5ee208a5b04f832e20bab19c60`. The published source identifies as
2.0.8.24F4; it is not asserted to match the working board's 2.0.8.26F4 binary.
The patch adds G39 and `Cap:E3_MATERIAL_HEIGHT_V1:1`, plus the read-only M119
axis-trust fields E3 expects. Both material touches use a separate -2 to +10.5 mm
contact interval in the native border frame. +10.5 mm corresponds to a 12 mm
sheet for this rig's recorded -1.5 mm support offset. Native fast touch, 5 mm
retract, slow touch and stow are retained; ordinary probing keeps its original
sample selection and checks. Missing contact and out-of-range touches fail.
G39 rejects before deployment unless native Z20, trusted Z, absolute millimetres,
leveling off, no Z workspace shift and sufficient complete-cycle headroom are
established. No parameter can widen the interval. It does not move XY or reset
the border datum. G39 omits G30's fixed return-to-bed-clearance move.

The host uses G39 for the border check and material only on the exact V1
capability; it rechecks the complete M115 identity each time. Stock firmware
keeps its existing G30 CLI path. Unknown/duplicate capabilities and malformed
results reject, with no fallback/retry. MachineService retains its existing
hardware/motion/confirmation/session/STOP gates and invalidates failed references.
Legacy desktop buttons remain blocked and camera-height compensation is still
not applied to tracing or jobs. The webcam and both production pipelines are
unchanged.

The firmware profile uses the identified RET6's 512 KiB flash and a conservative
64 KiB RAM region. It links at 0x08020200 after the existing updater metadata;
it never includes loader/updater bytes. A CMSIS SystemInit wrapper restores
interrupts after the updater's masked handoff. The unrelated laser feature and
automatic EEPROM initialization are disabled. ELF allocation/reset-flow checks,
Cortex-M4 execution of the actual startup wrapper with fake RCC/SCB registers,
and existing image validation pass. The final payload is 148080 bytes;
RAM allocation reported by the build is 10092 bytes. The package is
`dist/marlin-material-v1-7cdbc0e9`, application.e3fw SHA-256
`7cdbc0e9f7ae1ff862654e36766338ed09cc34bd0b88f00ec4df8c9c14fbd379`.
Its exact binary is retained independently of later documentation commits.

### Spare-board physical verification, 2026-09-07

The user explicitly superseded the earlier assistant-no-hardware restriction
for the plugged-in spare only and requested autonomous validation. The Pi,
working machine, webcam and their services were not operated or changed.
Windows enumerated one CH340 on COM6 (VID:PID 1A86:7523, location 1-1); the
existing client positively identified BENCH 0.2.0 before any upload. The board
is the previously identified CR4NS200141C13 / STM32F401RET6 bare spare. USB and
COM6/115200 8N1 were used; its supply voltage was not independently measured.

The assistant uploaded the final 7cdbc0e9 application through the retained
0.1.0 updater, received its verified-and-committed response, requested boot,
and obtained Marlin 2.0.8.24F4 with exactly `Cap:E3_MATERIAL_HEIGHT_V1:1`.
M119 reported all three axis-trust flags false. M114 reported
X -9.00, Y -6.00, Z 0.00, counts -720/-480/0. G39 returned
`Error:E3MH:1 PRECONDITION` and `ok`; the subsequent M114 was identical.
No G28, axis move, pin deployment, or positive-output command was sent.
The matching compiled branch rejects before deployment; unchanged coordinates
are not an electrical measurement of output pins. Firmware startup may perform
its native initialization; no attached probe/motor behavior was observed.

M115 identity was checked again before explicit M997. That reset successfully
returned to the retained updater, where HOLD was acknowledged. The exact
accepted BENCH 0.2.0 `f96d3a21` image was then uploaded, verified/committed,
booted and identified as BENCH. **The spare is back on BENCH 0.2.0.** Both the
0.1.0 and 0.2.0 recovery packages remain intact. This verifies normal Marlin
startup, bidirectional communications, unhomed G39 rejection and this maintenance
return/restore cycle. It does not verify cold boot, stock-loader byte identity,
power interruption, physical touch accuracy, fan voltage, or motor/probe motion.
Local raw transcripts remain under ignored `build/`, not in Git.

### Software verification

The real patched G39, material wrapper and run_z_probe functions pass compiled
Cortex-M4 execution with fake I/O for TOTAL_PROBING=2, TOTAL_PROBING=3, and the
published three-sample/one-extra configuration. Tests cover both touches,
missing contact, exact range edges, offset conversion, headroom, stow failure,
and unchanged ordinary probing. The actual final startup wrapper passes its
masked-interrupt/VTOR audit. All 187 focused Windows host/core/service/RPC/CLI
and secondary-owner tests passed; repository Ruff, compileall and diff checks
passed. A fresh isolated checkout reproduced every patched-source SHA-256.
The disconnect regression now waits for service cleanup after serial close and
covers both G30 and G39; the prior immediate assertion raced that cleanup.
Implementation commit 163f362 passed dedicated Windows Python 3.12 Marlin CI:
https://github.com/lukelave-boop/E3/actions/runs/34158944410 . It rebuilt the
pinned firmware, passed all three compiled probing configurations and startup/
package audits, and passed 95 focused host tests plus lint and compileall.
Fast Development CI also passed:
https://github.com/lukelave-boop/E3/actions/runs/34158944465 . The complete
Windows Python 3.12 desktop suite reported 4407 passed and 25 skipped; repository
Ruff and dependency/bytecode checks passed. These are automated checks, not an
interactive GUI, real camera, physical motor or physical contact qualification.

### Consolidated working-machine evidence from the two planning tasks

The last operator-reported Pi runtime was 0.7.31/fingerprint a89995ba. The operator
measured an approximately 2.1 mm piece as 1.94 mm, observing a completed fast/slow
cycle and return to Z20. This single 0.16 mm discrepancy does not qualify accuracy.
A 7 mm piece (5.5 mm above the border) received one touch and no contact-height
report; the clearance-derived early-trigger check in published Marlin is the
supported explanation, not proof of the installed binary's exact threshold.
These later observations supersede earlier blanket measurement-pending notes.

The operator's USB capture `ender-usb-ti0haryk.tar.gz` recorded stopped receive
requests before the failed Inspect: five stalls and one cleanup cancellation,
with no reported capture drops. Cable replacement did not cure it and autosuspend
was already disabled. The operator selected `[pi3] dtoverlay=dwc2,dr_mode=host`;
topology confirmed dwc2. Four checks over about seven minutes passed with the
webcam working, followed by another successful check after the requested longer
idle test (actual duration not explicitly confirmed). This is improved observed
stability, not a permanent cure or proof the webcam caused the original stalls.
The recorded Pi rollback is `/boot/firmware/config.txt.before-e3-dwc2`; it was
not accessed or changed in this task. The capture helper has therefore been
physically exercised, superseding earlier capture-pending notes below.

## Active: bare-board FAN1/FAN2, Z probe and Z axis bench application

The operator requested these four functions and has only the spare board, power,
USB, a meter and some switches. Repeated printer-harness swaps are not practical.
Application 0.2.0 therefore provides independent virtual fan percentages, virtual
probe deploy/stow/contact and bounded asynchronous virtual Z moves/searches, with
all physical output paths disabled. It separately samples real PC14 as an input
with weak pull-up; optional SWITCH source means a bench dry switch active LOW,
not a verified CR Touch polarity or connector pinout. The default SIM source
needs no wiring or external component. Firmware and host reports explicitly
identify BENCH mode and disabled physical outputs.

The updater stays at 0.1.0. New application uploads use the existing USB path and
image format. The accepted 0.1.0 image/package remains the recovery artifact.
A separate typed console handles status, inputs, fans, deployment, virtual
trigger, position, bounded move/probe, stop and reset; it accepts only the exact
BENCH identity and never becomes a production MachineService controller path.
No wiring changes, hardware commands or GUI operation are performed by the
assistant. The application and operator console are implemented and packaged.
The operator has now verified USB installation and the initial BENCH console
identity/status exchange, FAN1/FAN2 simulation, a virtual Z round trip and
simulated probe contact and missing-contact behavior on the spare. The bare
board PC14 input reports HIGH. Real input transitions and electrical output
levels remain pending. Earlier 0.1.0 acceptance
does not qualify the new application's behavior.

Local Windows verification: 227 Python tests passed (115 image/update host and
112 bench-console cases); focused Ruff, compileall and diff checks passed.
Fourteen compiled ARM core groups passed (seven retained updater groups plus
seven bench state/parser/timing groups). The existing production flash-wrapper
checks passed 27 cases; the new compiled production application with fake
UART/time/MMIO passed 23 cases, monitoring inactive GPIO requests, PC14 input
fields, raw reads and absence of flash writes. A contact first sampled at or
after the probe search deadline reports NO_TRIGGER, covered by boundary tests.
No GUI, electrical timing, physical component or real serial test was performed
as part of these automated checks.

The 0.2.0 image has a 4712-byte payload, CRC32 9A49FE26, application.e3fw SHA-256
0834abf9cdb664f59c3e012626009d5dce92f420dab2801d94d434e117379cd7.
The package is `dist/ender-aux-0.2.0-f96d3a21` (source SHA-256
f96d3a216692939f486f223ed460fc5f50ceb8bfd226e7c7212e36e135476a1b).
The updater padded to its 64 KiB region is byte-identical to the accepted
0.1.0 package (SHA-256 6af48a8c8cbb59f55641fa1bc5efc5404bc6717b3e2a2b1b1a538ee9e2a8f8d0).
Only the application.e3fw needs operator USB upload. BENCH_GUIDE.md contains
console commands and tests that need no added wiring or parts. This development
branch remains experimental and is not ready for production machine integration.

Dedicated Windows Python 3.12 firmware CI passed for source commit 8da170f:
https://github.com/lukelave-boop/E3/actions/runs/34137652240 . The run rebuilt
the package and passed the ARM core, both production-MMIO suites, all 227
Python client tests, lint and compileall. Every local packaged file matches
its manifest; the manifest records source revision 8da170f.

Operator 0.2.0 startup result, 2026-09-07: the identified CR4NS200141C13 /
STM32F401RET6 spare remained on the reported 24 V/USB-C CH340 bench setup,
Windows COM6 at 115200 8N1. Uploading the f96d3a21 package application identified
above returned `Application verified and committed. Updater remains active; use boot when ready.`
The operator then launched `bench.py --port COM6 --hardware-enabled` and reported
the initial prompt and state: outputs DISABLED, FAN1 0%, FAN2 0%, probe stowed,
simulated trigger 0, virtual Z 10.000 mm/target 10.000 mm, idle, result none.
The console accepts this state only after the exact BENCH 0.2.0 firmware identity.
This verifies upload, application execution and initial bidirectional console
communication on this spare. A boot-command acknowledgment was not supplied;
application execution is established by the accepted identity/status exchange.
At that initial startup, no control commands or PC14 transitions were reported. The outputs-disabled status is a firmware report, not a measurement
of connector voltages. No hardware was operated by the assistant.

Operator FAN1/FAN2 simulation result, 2026-09-07: on the same spare, BENCH
0.2.0 f96d3a21 application and COM6/115200 8N1 configuration above, the supplied
console transcript shows `fan1 50` -> 50%/0%, `fan2 75` -> 50%/75%, `fan1 0`
-> 0%/75%, and `stop` -> 0%/0% with result stopped. Throughout, the virtual Z
coordinate stayed at 10.000 mm, probe stowed, trigger 0 (SIM), motion idle and
outputs DISABLED. This verifies independent virtual fan state and virtual STOP
through the real board/USB console. It does not verify fan connector voltage,
PWM, current capability or attached fan behavior. No hardware was operated by
the assistant.

Operator virtual Z result, 2026-09-07: on the same spare, BENCH 0.2.0
f96d3a21 application and COM6/115200 8N1 configuration above, `move 2 1`
advanced the reported virtual position from 10.000 to 12.000 mm and completed
with idle/result done. `move -2 1` returned it to 10.000 mm, also idle/result
done. Both initial responses showed movement in progress. Fans remained 0%/0%,
probe stowed, simulated trigger 0 and outputs DISABLED. This verifies the
virtual round trip through the real board/USB console; no motor movement,
step pulses, electrical output levels or physical timing calibration were
verified. No hardware was operated by the assistant.

Operator simulated probe-contact result, 2026-09-07: on the same spare,
BENCH 0.2.0 f96d3a21 application and COM6/115200 8N1 configuration above,
`trigger off`, `deploy`, and `probe 5 0.2` started a virtual downward search
from 10.000 mm toward 5.000 mm. The subsequent `trigger on` returned virtual
Z/target 8.352 mm, idle, result contact, probe deployed and simulated trigger 1.
Fans remained 0%/0% and outputs DISABLED. This verifies stopping the simulated
search on a simulated contact through the real board/USB console. It does not
verify a physical probe input, probe actuation or motor stopping behavior.
No hardware was operated by the assistant.

Operator simulated missing-contact result, 2026-09-07: on the same spare,
BENCH 0.2.0 f96d3a21 application and COM6/115200 8N1 configuration above,
the operator set virtual Z to 10.000 mm, cleared the simulated trigger and
ran `probe 1 1` with the virtual probe deployed. The initial response showed
9.995 mm toward 9.000 mm with the search active; subsequent `status` showed
Z/target 9.000 mm, idle, result no_trigger. The probe remained deployed,
trigger 0 (SIM), fans 0%/0% and outputs DISABLED. This verifies the bounded
simulated search ending without contact through the real board/USB console;
it does not establish physical probe-failure handling or motor travel limits.
No hardware was operated by the assistant.

Operator probe-input baseline and post-search STOP result, 2026-09-07:
on the same spare, BENCH 0.2.0 f96d3a21 application and COM6/115200 8N1
configuration above, `stop` after the missing-contact search returned fans
0%/0%, probe stowed, simulated trigger 0, virtual Z/target 9.000 mm, idle,
result stopped and outputs DISABLED. `inputs` then returned
`Physical probe input PC14: 1`, with the same virtual state. This records a
real MCU input read reported through the operator console, consistent with
the configured weak pull-up on an unconnected input. It does not establish
connector pin mapping, measured voltage, a HIGH/LOW transition or CR Touch
behavior. No hardware was operated by the assistant.

## Verified spare STM32F401RET6 firmware bootstrap (0.1.0)

The operator supplied CR4NS200141C13 / STM32F401RET6 identification and owns SD access. Prior CH340 COM4 ROM-bootloader attempts at 115200 and 9600 opened the serial port but received no acknowledgment. No firmware was written in those attempts. The operator requested an upload-ready project preserving the original loader.

`firmware/ender_aux/` provides a standalone second stage at the published 64 KiB S1-family application offset, an inert application after a separate metadata area, and an explicit operator-run USB maintenance client. The C updater restricts erase/program to sectors 5-7, checks image metadata/vectors/CRC and commits validity last. No updater self-write, option-byte change, motor movement, probe pulse, laser output or air-assist ON is implemented. It is not a Marlin replacement ready for machine operation. The stock loader remains outside this code. The operator has now demonstrated SD installation and application startup on the spare; its exact SD erase behavior, loader-byte preservation and physical output behavior remain unverified.

Windows verification: Arm GNU 14.2.Rel1 compiled both images; 91 Python host tests and 27 compiled production-platform cases passed. Eight ARM-executed core test groups passed, including malformed commands, commit interruption, partial-upload recovery and application command rejection. Production disassembly confirms flash-busy routines/callees/literals execute from SRAM and no unresolved relocations remain. The build uses 0x20010000 for the initial stack to reach the MCU capacity check without requiring more than 64 KiB SRAM. Actual target remains RET6/512 KiB; the loader/application leave the unused SRAM available within the declared 96 KiB region. Dedicated Windows Python 3.12 firmware CI passed for source commit 2612d99: https://github.com/lukelave-boop/E3/actions/runs/34132731559 . It rebuilt the SD package and passed the host, compiled ARM core and production-platform tests plus lint/compileall. Local repository Ruff, compileall and diff checks also passed. This was the isolated firmware tier, not full desktop compatibility CI. No GUI, camera, serial controller, power or hardware operation has been performed by the assistant. Operator evidence below verifies one SD installation/application identity exchange and one complete same-image USB application upload, boot and identity cycle. A subsequent operator cold-start test also returned APP. The controlled incomplete-transfer recovery test below also passed. Output-state checks, UART timing margins, power loss during active flash operations, application-crash recovery and stock SD recovery remain physically unverified. The working machine and MachineService are unchanged. This experimental branch is not ready for production integration.

Operator physical result, 2026-09-07: on the identified spare CR4NS200141C13 /
STM32F401RET6, after the SD installation procedure, the operator ran
`host.py inspect --port COM6 --hardware-enabled` in Windows PowerShell and
reported `APP`. The selected package was `ender-aux-0.1.0-177e5965`, firmware
0.1.0 (source commit 2612d99; package revision 95f1d96), SD image
`e3aux_45271532.bin` (SHA-256
`452715322a2e6e347c8fa1b2cfbca57ad7a2e91b880785eeabcf0a1b66d3da1e`).
The reported bench setup uses 24 V and USB-C/CH340, with the maintenance client
configured for 115200 8N1 on COM6. The client prints APP only after accepting
`E3AUX1 APP 0.1.0 BOARD=0401E013`; this establishes application execution and
bidirectional USB-serial identity communication on this spare. It is operator-
reported evidence, not an assistant-run hardware test or a full flash readback.

Follow-up operator physical result, 2026-09-07: on the same spare, COM6,
115200 8N1 and reported 24 V/USB bench setup, the operator uploaded the same
package's `application.e3fw` (2380-byte payload, CRC32 `9E177C8B`, file SHA-256
`2a20429522ee398bdfa24b2565ad337e24afdf43eb3fcb725261ab66de27028f`).
The three separate commands and reported results were:

- `upload application.e3fw --port COM6 --hardware-enabled` returned
  `Application verified and committed. Updater remains active; use boot when ready.`
- `boot --port COM6 --hardware-enabled` returned
  `Boot requested; confirm the application with inspect.`
- `inspect --port COM6 --hardware-enabled` returned `APP`.

This records one successful application-to-updater transition, acknowledged USB
application erase/program with firmware CRC/commit verification, explicit boot,
and post-update application identity exchange. It verifies this normal update
cycle on this spare with this image; it does not establish interruption/crash
recovery, stock-loader byte preservation, or physical output states. No hardware operation was performed by the assistant.

Cold-start operator result, 2026-09-07: following the instructed SD-free shutdown
of both 24 V and USB and normal restart, with a ten-second delay before inspection,
the operator reported "ok power cycled. it returns APP". This verifies one
normal cold startup of the uploaded application without a host boot command,
on the same spare/configuration above. The later incomplete-transfer recovery
result is recorded below.

Added operator-only `interrupt-upload` to the Python host: validate the complete
recovery image before opening COM, identify/hold the updater, erase the application,
write exactly the first 64 payload bytes and wait for their acknowledgment, then
close without END or BOOT. It intentionally leaves the application uncommitted.
This exercises a transfer abandoned between completed flash writes; it does not
inject power loss during erase/program or commit. No MCU source/binary changes
are required. The existing accepted `177e5965` application's image remains the
recovery file. Host verification: 113 fake-serial/image tests pass, including
22 new recovery-test cases. Focused Ruff, compileall and diff checks pass. The
known application file still passes offline validation with CRC32 9E177C8B.
Dedicated Windows Python 3.12 firmware CI also passed for c78377f:
https://github.com/lukelave-boop/E3/actions/runs/34135682703 . It rebuilt the
package and passed ARM core/production-platform cases, all 113 host tests,
lint and compileall.

Incomplete-transfer recovery operator result, 2026-09-07: on the same
CR4NS200141C13 / STM32F401RET6 spare, firmware 0.1.0, Windows COM6 at 115200 8N1,
24 V and USB-C/CH340 bench setup, using the source host from c78377f/824dd67
and the existing `ender-aux-0.1.0-177e5965/application.e3fw` identified above:

- `interrupt-upload` returned `Application deliberately left incomplete after 64 bytes. No commit or boot was sent.`
- Following the instructed power cycle of both 24 V and USB with SD removed,
  and ten seconds without serial commands, the operator reported `UPDATER`.
- A full `upload` of the same application returned
  `Application verified and committed. Updater remains active; use boot when ready.`
- Following another instructed power cycle and ten-second startup wait, the
  operator ran `inspect` and reported `APP`. The restored image started through
  normal startup; no host BOOT command was used for this final recovery check.

This verifies one recovery from an abandoned, partially transferred application
across a cold restart, followed by full USB restoration and automatic application
startup. The deliberate stop occurred after completed/acknowledged flash writes.
It does not verify power loss while flash is busy, a torn commit, an application
crash, arbitrary corrupted/wrong-board image rejection on hardware, or recovery
from damaged updater/stock-loader bytes. No controller commands or physical
operations were performed by the assistant. The spare remains communications-
only; input reporting, probe actuation, Z motion and air-assist control are future
firmware work. This evidence does not qualify the working machine board.

This file records implementation and verification evidence. It is not an
operator procedure. Follow the canonical
[Permanent Camera Setup Runbook](laser_aligner/operator_docs/PERMANENT_CAMERA_SETUP.md)
for the current five-step calibration sequence and sixth read-only audit tab.

Snapshot: **2026-09-07**

## Active: Ender USB idle-failure diagnosis

The operator replaced the Ender USB cable; a successful Inspect was followed
roughly ten minutes later by another zero-response timeout. The reported Ender
USB device power/control is already on. Supplied kernel logs contain CH340
receive URBs stopped with -32. That identifies a receive-path failure, but the
underlying trigger remains unknown; webcam causation is not established.

Added a standalone, operator-run [USB metadata capture](docs/ENDER_USB_CAPTURE.md).
It requests no payload bytes, filters to the Ender adapter, has time/size bounds,
and reports capture loss. The webcam stays connected. It does not open serial,
change the application, recover connections, or send hardware commands.
Windows synthetic tests, focused Ruff and compileall pass; actual Pi usbmon
capture is implemented but physically unverified. No hardware was operated by
the assistant. Material-height validation remains pending below.

## Active: existing-controller Z offset measurement

### Probe failure evidence logging

Operator 0.7.30 evidence: reference-border and the subsequent G30 border check
completed; contact was -0.00 mm and final clearance was Z 20.00. The operator
confirmed expected physical behavior. A subsequent contact on paper directly
over honeycomb was rejected for a missing/unparseable contact report. The
operator observed deployment, one touch and a return to starting height. This
does not establish paper thickness or the cause of rejection.

The Pi journal previously retained only the parser error; detailed probe replies
were in a local memory log deliberately omitted from remote status. Asking the
operator to fetch that log through machine.status was incorrect. Probe failures
now write their existing transcript to the journal, preserving remote status
filtering and all motion/rejection behavior. Earlier missing replies cannot be
recovered through that status endpoint. No hardware was operated by the assistant.
Verification: the focused missing-contact RPC test passed and asserts the G30
reply appears in captured logging. Ruff, compileall and diff checks passed.

### Native border reference and single-contact height test

Operator acceptance on 2026-09-06: Pi 0.7.29/fingerprint `cc4c1cfe`, Marlin
2.0.8.26F4 (Jan 9 2023), completed request `ab50464a`: initial Z 0.03 to 5.03,
one G28 Z R0, known Z 5.00 after M420 S0, final Z 20.00. The operator described
the physical sequence as close enough to intended. This verifies that homing
test on this rig, not the new G30 measurement combination.

New source CLI reference-border/measure-height operations require their own
--confirm-height-test flag. Reference uses that native cycle and stores a
session-bound provisional border datum. First measurement must remain at the
border: one G30 X110 Y110 E1 must report contact within 0.5 mm of zero, with
homed/retracted state and verified final Z 20. The actual contact replaces the
provisional zero. The operator can then jog over material and request one G30
per reading without rehoming or resetting coordinates. Thickness subtracts the
border contact and signed honeycomb offset (-1.5 mm here). One sample has no
repeatability estimate. Contact acceptance is -2 through +15 mm in the border
homing frame; this does not alter firmware travel limits or prove clearance.

G30 owns deployment, internal touches and stow. Its internal sequence is not
claimed identical to G28. There are no raw servo commands or automatic repeats.
Legacy desktop Reference/Measure remain blocked until operator testing of this
new combination. No hardware, service, camera or GUI operation was performed by
the assistant. Session/home/STOP invalidation remains in force.

Windows verification for the new height path: **193 focused tests passed**
across native height/cycle, CLI, Z core/service/RPC and secondary ownership.
Tests use fake controllers and loopback RPCs; no GUI or physical test was run.
Repository Ruff, compileall and diff checks passed. The new test file is included
in the Linux/Pi compatibility job; full compatibility CI is a separate check.

### Single native fast/slow test requested by the operator

The complete operator terminal history established the first failure's cause.
On Pi 0.7.27/fingerprint `4dfe696e` (20 mm initial-lift implementation), one
native test completed: initial Z 0.03 to 20.03, one `G28 Z R0`, known Z at 5.00
after M420 S0, and final Z 20.00. The succeeding 0.7.28 attempt rejected that
position because it required reset Z zero. Its exception handler unnecessarily
issued STOP/secondary interruption before any Z move. A later attempt therefore
reported the shared controller disconnected. The old-process M106 timeout was
not the first cause of these new-process failures.

The fix admits reset/unhomed Z near zero or homed Z 20 clearance. Native pre-check
rejections preserve healthy connections; uncertain serial exchanges still close
the secondary without M112. Motion-active state begins before the first lift
attempt, preserving stop behavior after that point. Warning logs now include
the original error and whether motion had started. This is source/fake-test work
only; no hardware was operated by the assistant. The successful 20 mm-version
transcript does not physically verify the 5 mm-version or this follow-up.

Follow-up Windows verification: **157 focused tests passed** across native
probing, diagnostic CLI, Z core/service/RPC and the secondary controller owner.
Repository Ruff, compileall and diff checks passed. These used fake controllers;
the revised pre-check and connection handling remain physically unverified.

Follow-up: the operator requested a 5 mm initial lift. The host now verifies
that smaller relative lift before the same single native homing cycle; final
absolute Z 20 clearance remains unchanged. The Ender's own deployment clearance
is firmware-controlled and may add a lift. Primary XY Home / park does not prove
the probe's Z clearance. **51 focused native-cycle/CLI tests passed**, including
rejection of an uncompleted initial lift and suppression of final motion after
failure. Ruff and compileall passed. No physical action was performed by the
assistant; the revised sequence remains pending operator testing.

The operator asked to simplify the test to one native fast approach, backoff,
and slow approach. The new CLI `native-cycle --confirm-native-cycle` submits
the separate `native_test` operation through MachineService/Pi admission. It
requires the primary Home / park border pose, an Ender at reset or homed Z 20
clearance, a visibly retracted steady pin, disconnected secondary XY, laser unable
to emit and space for a 5 mm initial lift and final Z 20 clearance. Pin-only confirmation cannot
authorize this operation. No hardware commands or service restarts were performed
by the assistant; operator installation and physical testing remain pending.

The sequence verifies an initial relative 5 mm lift, runs exactly one
`G28 Z R0`, requires the Creality known-Z report and the historically observed
retracted logical Z 5 mm state, then verifies final absolute Z 20 clearance.
Firmware owns both approaches and pin handling. No manual M280, G30, retry or
extra homing cycle is sent. This changes Z origin and produces a test result
only, with no accepted border reference or material height. Reference/Measure
remain held. Published Creality G30 includes firmware-configured extra samples,
so reducing the number of G30 calls cannot promise exactly two internal touches.
Published source is not proof of the installed binary's behavior.

Windows verification: **235 focused tests passed** across the native cycle/CLI,
existing Z core/service/RPC, pin core/service/RPC and secondary owner. Coverage
includes initial lift rejection before homing, one native command, unexpected
home state, failed final retract, stale/unauthorized requests, cached request
replay and cancellation without another lift or retry. Repository Ruff,
compileall and diff checks passed. Tests use fake controllers/loopback sockets;
there was no GUI, camera or physical Z test. The new native tests are included
in Linux/Pi CI for the next run. This development branch is not release-ready.

The preceding pin diagnostic [CI run 34055605872](https://github.com/lukelave-boop/E3/actions/runs/34055605872)
passed Windows 3.10 and lint, but failed Linux's continuous-serial-chatter test
(`test_posix_serial_synchronization_rejects_continuous_chatter`, expected error
not raised) and the already recorded Windows 3.12 auto-home/STOP shutdown race.
No pin diagnostic test failed in that run. Those unrelated integration failures
remain unresolved; they are not counted as successful full CI.

Further operator pin observation: after another successful Deploy, gently
holding the pin upward did not retract it and caused red flashing. The operator
restarted the Ender before an input report could be collected, then reported a
retracted pin and steady light. Contact detection remains unverified; the manual
touch test did not establish a valid contact or identify a firmware fault.

### Operator-run pin diagnostics for the correction

Operator evidence on 2026-09-06: the Pi checkout/update reported `8430bc9` and
an active service. Inspect, Deploy and Stow then returned success on the same
primary/secondary sessions, reporting E3 0.7.26/fingerprint `701305a6` and
Marlin 2.0.8.26F4 (Jan 9 2023) on Ender-3 S1 Pro. The operator observed the
pin extend and remain down with purple changing to blue; M119 changed from
`z_min: TRIGGERED` to `z_min: open`. On Stow the operator observed retraction
and purple returning; M119 returned to `z_min: TRIGGERED`. M114 was unchanged
at X -10, Y -9, Z 0.03 throughout. These are one-cycle observations of pin
actuation and correlated input state, not contact detection, clearance, or
repeated G30 acceptance. The replies also explicitly report
`Cap:EMERGENCY_PARSER:0`; asynchronous emergency-parser support must not be
assumed for this firmware. No controller commands were sent by the assistant.

The operator has confirmed at least 10 mm of space below the retracted pin and
laser emission prevented for a pin-only check. They explicitly retain control
of every physical action: the assistant must not actuate axes, pin or laser,
connect hardware, or restart hardware services. This update is code and synthetic
verification only; installation and diagnostic invocation are operator actions.

Separate Inspect, Deploy and Stow actions now use the existing Creality owner.
They require an already connected primary and initialized secondary, disarmed
idle admission, acknowledged M5/M106 S0, and current primary/secondary sessions.
Deploy/Stow additionally require exact motion authority and explicit clearance
confirmation. Each sends one M280, settles for 0.8 seconds, and collects M119
and M114 after M115 identity. Inspect omits M280. No axis command, reconnect,
automatic retry/stow or M112 is added. Unknown physical pin state remains
explicit in the output; input/position reports cannot authorize a descent.
All actions invalidate prior height references/results. Full Reference/Measure
remain held while deployment and clearance are corrected.

The CLI is `python -m laser_aligner.probe_diagnostic`, using the authenticated
Pi action `machine.probe_pin`. Session compare-and-swap and replay caching
prevent stale or repeated requests from repeating actuation. An uncertain
reply closes the owner; bounded transcripts remain in success/failure responses
and logs. Initial operator-triggered deployment and stow are recorded above;
contact response and repeat probing remain unverified. The pin procedure is in
[MATERIAL_HEIGHT.md](docs/MATERIAL_HEIGHT.md#operator-run-pin-diagnostics).

Windows source verification: **254 focused tests passed** across the pin core,
CLI, MachineService/RPC integration, existing Z probe/secondary owner, and Pi
machine server. These use fake controllers and loopback sockets only. Repository
Ruff, compileall and diff checks passed. No GUI, camera, controller, pin or Z
physical test was performed by the assistant. The three pin test files are also
included in the Linux/Pi CI job; its result has not yet been recorded here.
Operator installation is confirmed above. The previously recorded Windows auto-home/STOP shutdown failure remains
an integration blocker, independent of these passing focused tests.

### Probe workflow withdrawn after observed undeployed descent

On 2026-09-06 the operator clarified that the earlier accepted reference did
not first raise before deploying the pin. During a subsequent sequence the
operator observed descent without the pin being dropped and cut Ender power.
The screenshot reports `G30 X110 Y110: Serial connection closed unexpectedly`.
Read-only Pi status confirms source 0.7.24/fingerprint `6a2d5fe6` (shorter-sequence
commit `379fc99`), no active job or accepted reference, disconnected primary and
faulted 115200-baud Creality owner. The pin behavior is operator-observed; the
disconnect is consistent with the power cut and does not identify its cause.
Earlier accepted results must not be read as physical safety acceptance.

Both Reference border and Measure Z offset are now suspended at MachineService
admission before any controller access. Status reports the feature unavailable,
a reason, and no usable reference. There is no configuration/RPC bypass. The
old Windows client receives the suspension error from the updated Pi. Synthetic
sequence tests explicitly bypass this constant only in fake-controller fixtures
to retain regression coverage; they are not evidence of safe pin deployment.
Deployment of the suspension update to the Pi remains pending.
Verification: **98 focused tests passed** across probe core, MachineService,
secondary owner and authenticated Pi RPC. New tests restore the production
suspension and prove both actions reject without primary or secondary writes,
including when a synthetic reference already exists. Repository Ruff,
compileall and diff checks passed. This verifies the admission block only;
the pin/deployment failure remains unresolved.

Review confirms the existing preflight deploys before establishing clearance,
and G30 repeats rely on firmware-managed deployment. M114 position and an open
M119 input are not established proof of a physically deployed pin on this rig.
Do not retry the current procedure. Further work needs the interrupted Pi
journal, a reviewed initial-lift strategy from unknown Z, and evidence for pin
deployment/release before guarded descent. No firmware change or speculative
replacement motion sequence is made in this withdrawal.

### Successful border reference and reduced travel follow-up

On 2026-09-06, after a complete Creality power/USB reset and Pi service restart,
read-only status confirmed the existing 115200-baud secondary owner ready with
acknowledged fan OFF and no fault. The operator then completed Reference border:
the Windows 0.7.21 panel displayed an accepted border and final Z 20.000 mm,
with support offset -1.500 mm. Pi source version 0.7.23, fingerprint `1b2d248c`
(diagnostic commit `a7a2494`), confirmed `reference_ready: true`. The same
Creality/CR Touch and unchanged firmware were used (historically identified as
Marlin 2.0.8.26F4; no new raw identity transcript supplied). This is operator
evidence of one successful border sequence, not known-thickness accuracy or
verification of the full material range. The operator observed repeated fast/
slow touches and excessive full-height lifts.

The follow-up retains three G30 reports but removes the full host lift between
them and the redundant full lift immediately after border G28. Firmware G30
still stows/returns; M114 must place Z at least 1 mm above the reported contact
and no higher than clearance +0.05 mm before another contact. These are software
checks, not newly verified physical clearances. One final full lift uses the
existing M400/M114 completion checks. Invalid contacts/readbacks stop the
sequence; no unsafe-height fallback or automatic retry is added. G28 and G30's
internal fast/slow touches remain firmware-controlled, so this does not claim
exactly one fast touch followed by three slow touches. The command sequence is
Pi-owned and works with the already-selected Windows 0.7.21 build.

Local Windows verification: **94 focused tests passed** across probe core,
MachineService, secondary owner and authenticated Pi RPC, including one final
full lift per operation and rejection of bad/missing intermediate clearance or
failed final retract. Physical verification of this shorter sequence and
known-thickness measurements remain pending.
Repository Ruff, compileall and diff checks passed. The preceding diagnostic
[Compatibility run 34052862195](https://github.com/lukelave-boop/E3/actions/runs/34052862195)
passed Linux/Pi and lint but failed the Windows 3.12 auto-home/STOP shutdown
test (`stopped` to `interrupted` terminal transition). A focused local rerun
reproduced that failure (one passed, one failed). That primary job-lifecycle
test does not use the secondary probe; no lifecycle code is changed by this
reduced-travel patch. The failure remains an integration blocker and is not
counted as passing probe verification.

### First operator attempt: timeout before visible movement

On 2026-09-06 the operator attempted Reference border with Windows 0.7.21 and
Pi checkout `64a10f6`. The dialog reported a secondary Creality acknowledgement
timeout; the operator reports neither probe-pin nor Z movement. Read-only Pi
status confirmed no accepted reference, no active job, a disconnected primary
and a faulted secondary. The failed command and partial secondary replies were
not exposed by that build, so the cause is not established. The displayed
support offset was still 0 mm; the reported -1.5 mm must be entered for a later
thickness result, but it cannot explain an acknowledgement timeout.

The diagnostic follow-up names the failed secondary command in the existing
error response, distinguishes silence from partial replies on timeout, logs a
bounded response tail, and retains the failed command in the probe transcript.
It does not change command order, motion, timeouts, acknowledgement acceptance,
STOP or automatic retry behavior. The change runs on the Pi and is compatible
with the selected Windows 0.7.21 client. Local Windows verification: **90 focused
tests passed**, including silent/partial M115 timeout diagnostics and existing
probe, secondary-owner, machine-service and authenticated Pi RPC tests; affected
Ruff and compileall passed. Later Pi deployment and operator observations are
recorded above. This was a diagnostic improvement, not a verified fix for the timeout.

### Implementation and earlier software verification

The same `codex/material-height-calibration` branch now implements **Machine
Setup > 7 · Material height**. This uses the existing Creality/Marlin controller
and CR Touch, sharing `CrealityControllerOwner` with secondary Air Assist. No
firmware, G38, G92, M851 or EEPROM changes are made. The operator references the
solid border at the existing Home / park pose, uses the ordinary primary Jog
path to position over material, and measures three contact heights. E3 subtracts
the three-contact border mean and the entered signed support offset to report
surface height and thickness above the honeycomb. Each set must agree within
0.10 mm; each retract requires completed motion and position readback. Missing
G30 contact data is a failure, never a measurement inferred from ACK or M114.

The explicit virtual-centre `G30 X110 Y110` addresses a possible cause of the
archived silent result: Creality's published G30 can return without probing when
its fictional XY is unreachable. This is an inference from public source, not
confirmation of the installed binary or of why that test failed. The published
probe code also has an early-trigger height check, so the full 12 mm range is
not yet verified. Start physical acceptance on the border and a thin known gauge.

Complete probe operations hold MachineService and Pi admission against competing
motion/START. They require hardware authority, `allow_motion`, a disarmed idle
primary, trusted XY, and explicit surface/clearance confirmation. The entered
20–80 mm Z clearance range is not a newly verified physical limit. Secondary
`M84 S0` keeps Z energized between reference and operator positioning without
saving EEPROM. References bind both controller generations and STOP; primary
Home, STOP, reconnect or changed clearance/support inputs require a fresh
reference. Detected monitoring-socket closure cancels the operation; silent
network loss is bounded by a 110-second deadline. Primary STOP precedes bounded,
generation-targeted secondary M112/close cleanup, which bypasses the ACK lock.
No automatic motion follows an uncertain operation. Motor hold and software
interruption are not safety-rated or physically verified here.

New fake-controller, service, authenticated loopback RPC and offscreen-widget
tests cover actual contact subtraction, no-contact/ACK-only rejection, parsing,
repeatability, retract, finite bounds, stale sessions, admission, request replay,
STOP, disarm, disconnect and closed monitoring connections. The broader focused
Windows run passed **395 tests**, covering the full machine-service module,
secondary owner/Air Assist, probe core/service/RPC and Machine Setup widgets.
Another **187-test** desktop/Pi/remote-client regression run passed (overlapping
coverage, not an additional unique-test total). Repository Ruff and compileall
passed. The probe panel was rendered offscreen and visually reviewed with the
production dark theme and Segoe UI; the capture remains outside Git. This
implementation has not moved physical hardware. It does
not install measured heights into camera transforms, projects, tracing or jobs.
The operator updated the Pi checkout to `64a10f6`, compiled the package and
restarted `e3-hardware-node.service`; systemd reported `active`. A subsequent
authenticated read-only capability request advertised `pi-creality-z-probe-v1`
and the new probe schema. No physical probe operation was issued.

[Compatibility run 34050906127](https://github.com/lukelave-boop/E3/actions/runs/34050906127)
passed Windows Python 3.10 core, Windows Python 3.12 desktop, Linux/Pi and Ruff
for `64a10f61adf75869bceffb2cea79e11c67fee6d2`. The exact revision was frozen as
Windows **0.7.21** using `packaging/build_windows.ps1`; the native-library guard
and installer build passed. Initial packaging attempts correctly rejected
foreign ICU/OpenSSL DLLs injected into child-process PATH by the execution
sandbox. The successful build used an isolated cache and clean environment
outside that sandbox, without bypassing the guard.

The exact EXE passed isolated offscreen and visible Windows startup checks
(15 seconds each, empty stderr, no startup-error log). Computer Use opened
Tools > Machine Setup > 7 · Material height and visually checked the frozen
panel with its motion controls disabled, then closed the isolated application.
This was an interactive offline UI check, not hardware verification. The
permanent **E3 DEV TEST** pointer was selected atomically with the validated
helper for **Probe material Z offset**, version **0.7.21**, revision **64a10f6**,
target `.codex-worktrees/creality-probe-build/dist/E3/E3.exe`. Physical border and
known-thickness acceptance remain pending; the branch remains active for that
verification and subsequent camera-height integration.
See [docs/MATERIAL_HEIGHT.md](docs/MATERIAL_HEIGHT.md) for the operator procedure.

## Earlier material-height calibration study evidence

Branch `codex/material-height-calibration` adds a profile-scoped two-height
calibration study. Original undistorted point observations at two measured
parallel planes fit one physical camera pose with fixed lens intrinsics; a
third middle height is held out of fitting. Signed heights use the fixed black
honeycomb border at Home / park as the proposed datum. Endpoint maps, source
identities and optical/machine provenance persist atomically under schema 1.
The UI saves maps and previews a height-specific image within the measured
interval. It does not activate the model in tracing, support teaching, projects,
job generation or execution; the camera study itself sends no controller commands.

The operator reports that Home / park naturally locates the probe over the
border and measured the honeycomb top **1.5 mm below the border** (2026-09-06).
Thus the reported signed support offset is **-1.5 mm**; paper directly on it is
at `-1.5 + paper_thickness_mm`, and the same paper on a 12 mm spacer is at
`10.5 + paper_thickness_mm`. Measurement method, uncertainty, reference-point
coordinates and probe repeatability are not recorded; this is an operator
measurement, not validation of an automated sequence. It is not installed as a
global machine configuration default. The operator also reports no firmware change since
the archived Z work (`bffecea`, tag `archive/s1pro-z-homing-safety-2026-09-05`).
That work homes onto a known surface; its recorded G30 acknowledges without
motion and G28 resets logical Z, so it is not an unknown-thickness measurement.
The preceding study therefore did not claim automatic measurement capability.
The operator-positioned implementation above now preserves the border reference
and requires contact reporting without re-zeroing, through the current shared
owner. Physical verification and explicit support/material-plane runtime
integration remain unfinished. The complete
design and operator study workflow are in [docs/MATERIAL_HEIGHT.md](docs/MATERIAL_HEIGHT.md).

Local Windows Python 3.14 verification passes **156 focused tests**, including
43 new geometry/persistence/AppContext/offscreen-widget checks. Tests predict
unseen intermediate heights and bed positions from a synthetic tilted camera,
reject stale/inconsistent/out-of-range inputs, retain independent check failure,
and prove study operations do not query hardware or alter the active bed map.
Existing base mapping, calibration provenance/profiles and Machine Setup tests
also pass. Repository Ruff, compileall and diff checks pass. The 920x700 study
dialog was rendered offscreen and visually reviewed with the production dark
theme and Segoe UI font; the image stays outside Git. No interactive GUI or
physical camera, probe, controller or laser validation is claimed.

Initial [Compatibility run 34046684566](https://github.com/lukelave-boop/E3/actions/runs/34046684566)
passed Windows Python 3.10 core, Linux/Pi and Ruff. Windows Python 3.12 passed
3,887 tests with 25 skips and failed one existing speed-display equality test:
its two generated programs crossed a wall-clock second and differed only in
the `Generated` timestamp. The test now holds the toolpath module's clock
reference fixed, retaining the complete byte comparison and leaving application
code unchanged. All 28 speed-display tests pass locally after the correction.
[Compatibility rerun 34047154094](https://github.com/lukelave-boop/E3/actions/runs/34047154094)
passed all four jobs on `b517047`: Windows Python 3.10 core, Windows Python 3.12
desktop, Linux/Pi and Ruff. The application/configuration/packaging tree is
identical to frozen source `15599de`; only the test and verification notes differ.

The required Windows packaging script completed frozen **0.7.18** from exact
source `15599de05bdb98f51b1e86201e00ecb3cbf6aedc` in the isolated
`material-height-study-build` checkout. Its EXE passed a 15.22-second isolated
offscreen startup check with motion disabled, a nonexistent controller endpoint,
no stderr and no startup-error file. The collected height modules were checked
against the isolated checkout paths. The native bundle guard and installer
compile passed. No physical device was used. The permanent E3 DEV TEST pointer
was selected atomically through `packaging/set_dev_test_feature.py` and matches
the adjacent version/revision metadata at
`.codex-worktrees/material-height-study-build/dist/E3/E3.exe`. The normal E3
launcher and prior frozen bundles are preserved. The later test correction and
verification notes have no application, configuration or packaging diff against
the exact frozen source. This remains a development study pending physical
evidence and the remaining production/probe implementation, not a completed
automatic-thickness feature or a main-branch integration.

## Physical stall evidence: 2026-09-06 08:11

Operator-provided Pi service, automatic ACK evidence, and kernel excerpts record
job `a607d6c6` accepted at 08:10:53.988, then primary GRBL silence around
08:11:15.808. Generation 1 on `/dev/ttyACM0` timed out on transaction 6238,
`G1 X73.932 Y67.164 F3000`, at 08:13:15.819 with 781/3008 lines completed.
The 25-byte host write completed. The reader/receiver had recent polling
checkpoints, no new raw reads for 120 seconds, no partial frame, matching
published/consumed line counts, and no pending receiver reply. This establishes
missing input at the observed raw serial boundary; it does not establish device
receipt of the move or prove which USB/driver/controller component failed.

The kernel reported a CH340 `ttyUSB0` bulk-read completion error `-32` at
08:12:56.274, about 100 seconds after primary silence began. This is the
secondary connection in the recorded rig, not the primary ACM endpoint. USB
host `dwc_otg_hcd_urb_dequeue` warnings followed at 08:13:15.850, after primary
quarantine at .841 and during recovery. Their cancellation-path timing is
consistent with cleanup exposing outstanding transfers; it does not locate
the cause of the original stall. Fresh generation 2 answered handshake commands
at 08:13:18 and recovery required Home, without resuming the failed job.

USB/driver/device communication is the leading investigation path. There is
no affirmative evidence here of a reply trapped in E3's queues, a currently
blocked reader, a controller alarm, or undervoltage at the silence onset. Kernel
warnings alone do not establish a bad cable, inadequate power, or firmware bug.
The operator subsequently identified a Raspberry Pi 3 Model B Plus Rev 1.4,
kernel `6.18.34+rpt-rpi-v8`, and deployed source
`7ceeaeab2f9a1c7cc210f83e287eb23399d52ea7`. Its primary CDC ACM controller,
secondary CH340, webcam, and Ethernet share the `dwc_otg` USB host. The primary
and secondary boards have separate supplies; their motor/fan loads must not be
attributed to Pi USB power. The controller/Pi execution sources have no diff
against the current integration revision. Exact controller firmware, measured
power delivery, and stall-time USB transfers remain unknown. Source-level
interpretation is not physical verification of a particular cause. Next
discriminating evidence is USB transfer submission and
completion around the first lost reply. No machine behavior or recovery policy
has been changed for this investigation; raw operator logs remain outside Git.

### Subsequent power and stall observations

The earlier hardware snapshot reported `get_throttled=0x0` and 49.4 C. The
operator later substituted a wall USB supply rated 3 A and reported another
stall, then tried another cable. A subsequent `0x50000` sample records historical
undervoltage and throttling with the current-condition bits clear; it does not
timestamp either condition or tie it to a particular stall.

A later kernel excerpt has boot messages around 09:24:14, a USB device reset
at 09:24:38.974, and another secondary `ttyUSB0` read error `-32` at
09:27:29.615. Fresh undervoltage detections at 09:28:06.535 and 09:29:15.083
each clear about six seconds later. Thus voltage detection events occurred
during this boot, but the first shown event follows the secondary USB error
by about 37 seconds. The subsequently supplied full service log identifies a
different initiating failure for job `94282592`, accepted at 09:27:08.989:
at 09:28:33.004 the secondary Creality acknowledged-response timeout fails the
job. The primary had still acknowledged a `G1` at sequence 3024 and two `M5`
commands at sequences 3025/3026 at 09:28:31. This is not the earlier primary
120-second ACK silence. The tail is compatible with a late program OFF or
post-stream secondary OFF failure, but the stored program and AUX transcript
are needed to identify the exact boundary. Failure invalidates primary trust,
skips successful Home/park, and recovers to Home required. Later job `b83ef520`
was accepted at 09:29:22.219; this excerpt has no terminal result for it.
This boot also identifies USB device 6 as the C920, so the 09:24:38 reset
belongs to the camera near startup, not the subsequent job failure.

The generated desktop program can end a path with `M5`, append another footer
`M5`, then issue `E3AIRASSIST OFF` before its final standalone `M5`. Thus the
acknowledged pair does not prove the whole immutable program completed. The
stored job metadata (completed line count) and program tail are the next
read-only evidence needed. AUX/TX/RX entries are bounded in-memory logs and
DEBUG journal messages; the normal INFO node log does not durably preserve
their full transcript. Failed program retention is bounded, so preserve the
matching job artifacts before further jobs can evict them.

The replacement-cable/cold-boot sequence and whether this run was laser-off
remain unconfirmed. The operator reports a
short cable and a 3 A supply; these ratings do not measure the voltage delivered
at the Pi or identify the source of the warnings. Power delivery is a separate
observed concern, not an established explanation of every primary stall.
No further reproduction or powered validation is recorded as successful.

The operator then powered the Pi from an old PC supply's 5 V rail through the
5 V/GND header, explicitly without an inline fuse, and reported another stop.
At capture time 09:51:26 the Pi returned `0x50005` (current undervoltage and
throttling plus their historical flags), with temperature 45.1 C. Its kernel
excerpt reports undervoltage at 09:43:04.790 and no normalization in the supplied
window. Bypassing the connector has not established stable voltage at the board.
The operator measured about 4.85 V at the PC supply without the Pi load; no
loaded Pi-side voltage measurement has been supplied. A protected/fused feed
and measured voltage under idle/camera load are needed before further job tests.

For this latest run the CH340 read error at 09:50:14.498 precedes a pre-start
secondary OFF timeout at 09:50:25.118. The existing fresh-session retry then
acknowledges OFF at 09:50:27.293, and job `9cb08d0c` starts at 09:50:27.475.
The collected service log ends there, approximately 59 seconds before capture;
it contains no initiating job failure or terminal record for the reported stop.
Do not classify that pre-start recovered failure as the later job failure or
assume this is another primary ACK timeout. The previous primary-silence and
secondary-OFF failures remain distinct observations pending causal evidence.

The operator next used a 5 V / 2.5 A DIN supply adjusted to a measured 5.08 V
and ran the same job. `get_throttled` returned `0x0` at both 10:13:06 and
10:13:19; the operator explicitly identifies one sample as during laser work
and the other as after the laser job. These are clean current and historical
power/throttling flags for that run, contrasting with the preceding `0x50005`
sample. The voltage measurement location, fused-feed implementation, and final
Home/park result have not been explicitly confirmed. This is evidence of an
improved reported power condition during one run, not proof that either prior
communication failure has been eliminated or caused solely by undervoltage.

## CI checkout correction

Retention commit `30f9543ddf3c2f57cab968361503913108800975` accidentally included
eight local linked worktrees as mode-160000 Git entries without `.gitmodules`.
[Compatibility run 34033923759](https://github.com/lukelave-boop/E3/actions/runs/34033923759)
failed all four jobs during checkout credential cleanup with `No url found for
submodule path`; no tests or Ruff checks ran. This was a repository-checkout
failure, not evidence of four independent application regressions.

The correction removes precisely those eight index entries and ignores new
content under `/.codex-worktrees/` and `/worktrees/`. Existing tracked ordinary
files under those paths and all local worktree folders are preserved. The
selected E3 DEV TEST executable remains present at its original frozen revision.
A clean local clone reproduced the original error; applying the index-only
correction made the same recursive submodule command succeed. All eight local
worktrees were checked for preservation and ignore coverage; no Git entries
with mode 160000 remain in the corrected index. Correction
`45f533b09fdd8d8990b44d0ec2becd481c96c79a` passed
[Compatibility run 34034721004](https://github.com/lukelave-boop/E3/actions/runs/34034721004):
Windows Python 3.10 core, Windows Python 3.12 desktop, Linux/Pi serial/recovery,
and Ruff all succeeded. Local Ruff, compileall and diff checks also passed.
Application, controller and publisher code is unchanged. The follow-up commit
records only this completed verification; it does not change the tested code.
The accompanying [publication run 34034721012](https://github.com/lukelave-boop/E3/actions/runs/34034721012)
also completed successfully.

Publication currently runs independently of Compatibility CI, so its successful
package-integrity checks do not establish that the compatibility suite passed.
CI-gated publication remains a separate workflow improvement.

## Active: development release retention

The rolling development publisher now protects the current Windows/Linux pair
and two recent complete pairs. Other recognized packages receive a dated asset
label and are eligible for deletion only after at least seven days retired,
on a later successful publication. Re-promoting an older build clears its
retirement labels before switching the live manifest. Cleanup rechecks manifest
and asset identity, preserves unknown assets and recovery backups, and warns
without rolling back a working update if cleanup fails.

A read-only plan against the current GitHub release inspected 49 uploaded
assets: 42 would begin grace, zero would be deleted, and the current `38ee99bb40cc`
pair, prior `1afe4fb3fe8b`/`831b352cb0d7` pairs, and live manifest were protected.
That investigation changed no remote assets. The subsequent
[publication run 34033923698](https://github.com/lukelave-boop/E3/actions/runs/34033923698)
successfully published `30f9543` and started seven-day grace for 44 older package
assets. No expired-package deletions occurred; the first eventual deletion cycle
remains unverified. Compatibility testing was blocked by the checkout defect
described above.
There are no desktop, updater download-format, or machine-runtime changes.

Verification: 70 focused publisher/updater/release tests passed on local Windows
Python 3.14; two existing symlink-security tests skipped because this account
cannot create Windows symbolic links. The publisher's 49 tests cover first
retirement, the exact seven-day boundary, recent/current protection, mandatory
grace reset before promotion, interrupted cleanup, metadata changes, malformed
metadata and paginated assets. Repository Ruff, publisher compileall and
`git diff --check` passed. These are API-fake tests plus a read-only live metadata
plan, not a live deletion test; the first publication/retirement run is now
recorded above. Full compatibility subsequently passed on the checkout correction
as recorded in the CI section.

## Main consolidation

At the operator's request, `main` now integrates the completed auto-Home/layer,
upload/status, desktop readability, and serial-diagnostics work. Feature tip
`1e61b6c0b0bc8719382d6fc23ae52a1cb749434d` already contains diagnostics tip
`151a504bb3a70d096974206308bad911b249d941`; main advances without replacing newer
code or resolving conflicts. The root checkout uses `main` again.

Pre-integration [compatibility run 34030594529](https://github.com/lukelave-boop/E3/actions/runs/34030594529)
passed all four jobs on that feature tip: Windows Python 3.10 core, Windows
Python 3.12 desktop, Linux/Pi serial and recovery, and Ruff. The consolidation
adds only these verification notes to the tested tree; no runtime code changes.

Completed development refs are removed after publishing the integrated main
tip. Detached worktrees, the existing 0.7.10 E3 DEV TEST target, local scratch
files, stashes, and the `archive/s1pro-z-homing-safety-2026-09-05` tag are retained.
This is branch consolidation, not filesystem cleanup. The previously recorded
Pi shutdown race and intermittent physical streaming stall remain known gaps;
consolidation and passing automated checks do not claim to resolve either.

## Active: desktop columns, percentage speeds, and Preview layout

The operator requested manually resizable columns throughout the desktop,
percentage speed presentation, and a readable initial Preview layout. All twelve
desktop column views now share Interactive headers, readable initial widths,
and native divider dragging/double-click fitting, including the last column.
Manual widths survive data refreshes for the lifetime of the view.

Job/layer/material/marking speed uses the running maximum work feed as 100%; Jog
uses the running maximum travel feed. Exact mm/min and the reference remain in
tooltips. Engineering limits stay physical values. Opening, refreshing, and
changing the display reference preserve exact saved feeds and do not silently
clamp over-limit data. Existing project/material schemas, G-code feed values,
machine safety checks, and primary/secondary serial behavior are unchanged.

Preview starts near an equal canvas/details split, puts operation names and
speed/power first, and uses compact tables for small jobs. Longer tables and
narrow windows scroll. Preflight titles/messages precede technical codes, and
the canvas/details divider remains draggable.

Offscreen mouse tests cover every column in all twelve views, divider
double-click, and width retention after refresh. Percentage tests use real
keystrokes for fractions and invalid input, preserve exact legacy feeds, and
check unchanged G-code on display-only round trips. The final Preview/speed
checks passed 51 tests, header-related checks passed 96, and the updated dock
harness passed all ten layout checks. Repository Ruff and compileall passed.
Rendered Preview was reviewed at 1320x820 and 900x680; a viewport-containment
test keeps the warning summary initially visible at 700x520 with six operations.

The initial local Windows Python 3.14 run passed 3,776 tests with 28 skips and
found ten outdated dock-harness fixtures (corrected above) plus one Pi auto-Home
shutdown race under four-worker execution. The final full run on frozen source
`5a6122b76bcf47109d9efc0ea8628edf4eb757ca` passed **3,800 tests, 28 skips**.

The separate shutdown race was reproduced deterministically: `_update_terminal`
persists `stopped` before clearing `_active_job_id`, while concurrent shutdown
tries to classify that still-active ID as `interrupted`. The job store correctly
rejects replacing a terminal outcome. The Pi code is unchanged by this desktop
revision; this needs a separate lifecycle correction and does not explain the
intermittent physical streaming stall. The final full-run pass does not resolve
this scheduling-dependent finding.

The operator reports that the 0.7.10 feature build looks good and has explicitly
approved pushing the desktop changes and verification notes to the public
development branch. This is general operator feedback, not an additional
controller or laser test record. Supported-version compatibility CI passed on
the published feature tip as recorded above.
The required Windows packaging script completed for **0.7.10**, source
`5a6122b76bcf47109d9efc0ea8628edf4eb757ca`, in the isolated
`readable-desktop-build` checkout. The native bundle guard passed and the frozen
app stayed alive for an isolated 15.2-second offscreen startup check with no
startup error or stderr output. The permanent E3 DEV TEST pointer was selected
atomically through `packaging/set_dev_test_feature.py` and matches that EXE's
adjacent version/revision metadata. Installed production E3 was not changed.
The operator feedback above is recorded separately from automated verification;
no additional real camera, controller, or laser verification is claimed.

## Active: upload/start status and progress

The operator reports START taking more than ten seconds and desktop screenshots
switching from upload/start to STATUS UNAVAILABLE / STATE UNKNOWN. The Pi
diagnostic update was deployed per operator confirmation; no new timeout
evidence or measured upload/start breakdown has been collected yet.

Code and loopback tests identify a shared-lock defect: coherent machine status
queried the durable job store while chunk fsync or finalize validation held its
transaction lock. The store now publishes a bounded committed observation under
a separate short lock after successful durable writes; machine/job status reads
that observation without disk I/O. Execution admission, byte integrity, START,
STOP, and recovery retain their durable checks. Machine-status response metadata
now comes from the exact sampled body instead of sampling controller state again.

Windows maintains a local submission phase separate from Pi job ownership and
machine authority. It displays acknowledged upload percentage, then verification
and starting without invented percentages. A recent-contact freshness gap says
CHECKING STATUS in amber; an explicit failed machine poll, lost contact, or
controller fault still takes precedence. Snapshot freshness, motion gating,
STOP availability, and accepted-job independence remain unchanged. Advancing
controller metadata requests an immediate monitor refresh. Timing records split
upload, verification, and START in the desktop log without program/token payloads.

With a test store lock held for 350 ms, the prior loopback status path waited
369 ms; the corrected path replied in 2.24 ms while the lock remained held.
Tests holding actual chunk fsync or finalize validation also keep machine and
job queries responsive while exposing only the previous committed record.
These are Windows loopback measurements, not Pi or physical controller timing.
The Pi-focused Linux checks passed 109 tests; final combined verification follows.

Secondary Air Assist classification previously serialized/hashed its mapping
twice even for ordinary G1 lines, repeated across independent safety analyses.
It now recognizes non-directive lines before that calculation; exact directive
and malformed/foreign-mapping rejection are preserved. An alternating five-run
Windows benchmark of a 9,006-line program produced equal ValidatedProgram values
and median preflight times 1.134 s before / 0.474 s after under concurrent load.
This measures local preflight CPU savings, not end-to-end physical Pi START.
The classifier/dialect/secondary integration checks passed 90 tests.

This existing feature branch now includes the verified serial diagnostics and
connection-retry correction alongside automatic Home and layer assignment, so
the next matched desktop/Pi handoff preserves those changes. Physical validation
remains pending for this revision.

Combined Windows Python 3.14 machine/Pi/desktop checks passed 602 tests with
two explicit POSIX skips. The final compact-panel/classifier checks passed 23
tests; repository Ruff and compileall passed. Desktop verification includes
offscreen widget interaction and reviewed rendered strips/progress bars.
No real camera, controller, laser, or interactive operator test was performed.

Compatibility run
[34027973127](https://github.com/lukelave-boop/E3/actions/runs/34027973127)
passed at 7ceeaeab2f9a1c7cc210f83e287eb23399d52ea7: Windows 3.10 core 3160 passed /
73 skipped; Windows 3.12 desktop 3767 passed / 25 skipped; Linux serial/session
411 passed; Ruff passed. The exact frozen Windows build is 0.7.8 at that revision,
produced with packaging/build_windows.ps1 in the isolated upload-start-build
checkout. Bundle validation and a 15-second isolated offscreen launch passed
with no startup errors, camera autostart disabled and hardware authority absent.
The permanent E3 DEV TEST pointer selects this exact build. The matched Pi
update is pinned to the same revision and includes a rollback tag; it was
syntax-checked but has not been executed by the agent. The normal installed E3
application remains unchanged. This development branch awaits operator testing.

## Active: automatic Home and object layer assignment

`codex/auto-home-object-layers` follows the operator's reported successful 0.7.0
Pi update and regular Windows application update. This report does not establish
new firmware/configuration measurements or validate the new behavior physically.

MachineService now owns conditional Home/park as part of one immutable prepared
job start, binding STOP, authorization, and session generations across the
sequence. Pi durable job ownership and duplicate-START handling remain in place.
Desktop explicit HOME_REQUIRED permits Start but still rejects direct arming.
Layer assignment is consistently selected-object scoped in Cuts rows, bottom
tiles, and the Cuts dropdown; each Objects-row dropdown targets only that row.
Selection refresh follows actual layer IDs, shared settings remain shared, and
assignment uses existing undo/persistence and generated-job invalidation.
Final interaction review added a regression for Output/Show checkbox focus:
these shared layer edits do not reassign shapes, and later explicit dropdown
assignment still works. The frozen candidate is rebuilt with this correction.
The affected desktop/trace regressions pass **41 tests** after this correction
and updating the trace-selection harness for active-layer presentation. Candidate
CI 33985655932 passed Windows 3.10, Ruff, and Linux; Windows 3.12 reported only
eight missing-active-layer-field failures in that old test harness (3705 passed).
The final candidate receives a fresh full compatibility run.

Source verification: Windows Python 3.14 affected regressions **601 passed**;
WSL/Linux Python 3.10 focused Pi/session/serial checks **202 passed** (one benign
pytest cache-permission warning). Repository Ruff and compileall pass. Desktop
coverage is offscreen widget interaction plus reviewed offscreen panel renders;
the exact frozen build and supported-version CI outcomes belong to the feature
handoff. Tests preserve the fail-closed legacy-status gate and cover per-object
assignment, undo/redo, persistence, distinct generated feeds, full automatic
Home, repeated jobs, and failed/cancelled starts. Pi socket tests pass for
connection loss during conditional Home and STOP cancelling the pending start.
No physical controller, camera, laser, or interactive operator test has been
performed for this feature yet. Required validation: Start from HOME_REQUIRED,
repeat from held READY_MOTION, STOP during automatic Home, failed Home, and
independent layer assignment through each dropdown/row/tile followed by Preview.

## Active primary serial timeout evidence

Production Windows/Pi 0.7.0 (`1afe4fb3`) intermittently stopped while streaming
ordinary F3000 G1 segments. Operator journals identify failed jobs e9153390,
5ed473c0, and 18db9cc9, with successful jobs between failures. The last case
correlates a Linux write accepting all 24 bytes at 13:34:03.840 with no further
read syscall in the remaining approximately 20 seconds of the capture, then
an acknowledgement timeout at 13:36:03.845. Recovery created a fresh generation
and required Home. The controller endpoint was ttyACM1 / Espressif Device by-id;
firmware identity was not recovered. These are physical failure observations,
not evidence that firmware, USB, temperature, or E3 is the root cause. The
capture omitted select/readiness waits and cannot rule out a blocked reader.

The `codex/serial-timeout-evidence` revision adds observational checkpoints to
POSIX reader/writer/queue delivery and primary receive dispatch. Before ACK
timeout unwinding, it logs one bounded JSON evidence record per session with
the exact command/sequence/generation, job progress, checkpoint ages/counters,
partial-byte evidence, and targeted thread code locations (no frame locals).
Snapshotting does not acquire serial/ingress operational locks or consume input;
busy diagnostic/ownership gates are reported without waiting. Cleanup cannot
replace the first record. No controller commands, timeouts, retries, arming,
reference, or stepper policies change. This is diagnostic instrumentation, not
a stall fix or watchdog; it only runs when the existing ACK timeout is reached.

Focused local verification: Windows Python 3.14, 189 passed / 2 POSIX skips;
WSL Linux Python 3.10, 54 passed (pytest cache permission warning only). Includes
missing-ACK job failure/abort, first-evidence retention, broken snapshot provider,
held ownership lock, blocked reader, partial reply and real PTY CR/CR/LF delivery.
Ruff and compileall pass. Additional Windows integration checks passed 112 tests
with two POSIX skips. Compatibility run 33988310114 passed Windows 3.10, Linux,
and lint; Windows 3.12 passed 3699 tests but failed the existing 1000-session
soak's successful three-frame query using a 20 ms deadline. A temporary probe
with 15 ms receiver scheduling delays exceeds that budget with both the baseline
and instrumented receivers; both succeed with a one-second deadline. Only that
soak success assertion now allows one second. Its explicit 1 ms missing-ACK
injection and every production deadline remain unchanged. Final reverification
is recorded below; deployment/physical validation remain pending.

The second compatibility run (33991051612) passed Linux (392 tests), Windows
3.10 (3111 passed / 72 skipped), and lint. Windows 3.12 passed 3699 tests but
exposed a separate existing connection-retry defect: recording only object ids
allowed an earlier failed transport to be collected and its id recycled for a
fresh third candidate. The retry guard could then stop after two opens. A
deterministic weak-reference/collection test reproduces loss of that identity.
The bounded retry loop now retains candidates and compares object identity;
reusing the actual same transport remains rejected. This affects connection
attempts, not an established job's ACK wait, and is not a mid-job stall fix.
The corrected Windows Python 3.14 machine/session/diagnostic checks passed
380 tests with two POSIX skips; focused WSL Linux Python 3.10 checks passed
67 tests (pytest cache permission warning only). Ruff and compileall passed.
Final compatibility run
[33991802258](https://github.com/lukelave-boop/E3/actions/runs/33991802258)
passed for b4c026d: Windows 3.10 core 3113 passed / 72 skipped; Windows 3.12
desktop 3702 passed / 25 skipped; Linux serial/session 392 passed; Ruff passed.
These are automated software checks, including offscreen desktop tests, not
physical controller, laser, or camera validation. The prepared Pi handoff pins
b4c026d3a1f835fc556f161c2553293331537763; no Windows rebuild is required.
The intermittent physical stall remains unresolved, and this development
branch is awaiting Pi deployment and operator evidence before integration.

The subsequent 13:50:07-13:50:12 raw trace shows the ttyACM1 reader repeatedly
returning empty 100 ms readiness waits. The reader was polling during that
window; whether the capture preceded recovery has not yet been confirmed.
It does not establish the controller/USB root cause.

The diagnostics are now included in this auto-Home/object-layer development
branch; production 0.7.0 does not include those features.

## 0.7.0 consolidation

The operator confirmed the final post-timing-fix STOP -> Home/park -> next-job
sequence on 2026-09-05 and requested consolidation into main as 0.7.0. Together
with the reported speed recovery, this closes that specific operator retest.
The Pi source was c016e1978a3e3a5f16b1356e9036b26acfe24008; Windows was frozen
0.6.202/a152b50. The previously recorded GRBL setup/settings identify this
evidence; firmware was not queried again, and no measured speed ratio was given.

Compatibility run 33980957659 finished with one identical failure on Windows
3.10, Windows 3.12 desktop, and Linux: primary Marlin's post-job M84 was missing.
History identifies 5165777's GRBL hold change as the accidental removal. The
correction restores acknowledged release only for the primary non-GRBL dialect
after successful Home/park. GRBL retains $1=255 and READY_MOTION; the secondary
Air Assist controller is not a target of this release command. A rejected M84
must fail the job, clear reference/arming, and retain the initiating diagnostic.
Both transcript outcomes pass locally. This Marlin correction is software
verified, not physically tested on a primary Marlin controller.

The 0.7 series uses immutable tag v0.7.0 as its commit-count baseline. The unique
S1 Pro Z-homing branch is excluded with operator approval and preserved at
`archive/s1pro-z-homing-safety-2026-09-05` (bffecea). The paused root serial EAGAIN
experiment and physical notes are excluded and preserved in the named local
stash `pre-0.7.0 paused serial EAGAIN experiment and physical notes`, with
matching file backups under ignored build/consolidation-0.7.0. Local Linux
release checks passed 356 tests.
Local Windows 3.14 full xdist run: 3692 passed, 26 skipped, one updater shutdown
timing failure (successful exit at 3.84 s exceeded a 3 s assertion); its full
11-test module passed on rerun. Ruff and compileall passed. These local results
do not replace the required Windows 3.10/3.12 compatibility checks.

Candidate CI 33981766662 exposed a runbook version-label mismatch after the
minor bump. The packaged runbook now identifies the 0.7 series. Old ignored
build-info.json in the local worktree had masked this under the runtime
metadata fallback; it was moved aside before rechecking the release version.
The seven version/runbook checks pass with the actual 0.7.0 runtime version.
The release compatibility record is
[run 33982196816](https://github.com/lukelave-boop/E3/actions/runs/33982196816).
Integration requires green Windows 3.10 core, Windows 3.12 desktop, Linux
controller-session, and lint jobs. This final documentation record changes no
production or test code after that candidate. Frozen build identity and its
isolated offscreen launch result are reported in the release handoff; neither
that check nor offscreen widget tests constitute a real camera/controller test.
Historical entries below retain their original context and are superseded by
this consolidation record where they describe earlier integration blockers.

Remaining audit work: automatic Home followed by the exact prepared job on
START, secondary lifecycle hardening, independent cooling-fan capability, and
broader failure-sequence physical validation. These are not part of 0.7.0.

## Active Pi status authority and repeat Home revision

Operator follow-up after deploying Pi `c016e1978a3e3a5f16b1356e9036b26acfe24008`:
reported that the correction solves the speed issue. The deployment screenshot
shows successful imports and active/running service PID 87415, with rollback
reference pi-before-receive-timing-20260905T172729Z. Windows remains 0.6.202.
No measured 1500/3000 cutting times or explicit post-update STOP/Home retest
result was provided, so this is reported symptom resolution rather than full
physical acceptance. The previously reported GRBL controller/settings and
verified source hashes identify the setup; firmware was not re-queried.
Compatibility run 33980957659 remains an integration blocker: lint passed,
Windows 3.10 and Linux failed, and Windows 3.12 was still running at this check.
Before another feature, close the remaining validation gaps and resolve CI;
the next planned feature remains exact prepared-job automatic Home on START.

New speed investigation after the successful 0.6.202 notification retest:
operator reports that changing the previewed feed from 1500 to 3000 did not
visibly change physical speed. Exact comparison geometry and cutting-versus-total
duration have not yet been confirmed. Production code/configuration is unchanged.
Local generation of a 30 mm ellipse emits 72 G1 segments with the requested
F1500 versus F3000. MachineService rejects excessive feeds instead of silently
clamping them. The streaming path waits for each ACK and a 10 ms quiet boundary.

Authenticated read-only Pi machine.status returned READY_MOTION, a successful
82-line powered job, and the configured Espressif Device by-id endpoint. Its
retained transcript shows approximately 15 short F1500 moves between 11:07:23
and 11:07:26, followed by Home/park. Post-job controller settings report
$110=$111=10000, $120=$121=500, and $1=255. The Pi advertises version 0.6.200
but revision `723b7eb7`, not the previously assumed deployment SHA; verify the
actual Pi checkout/source before attributing all local findings to deployment.
The operator subsequently supplied HEAD `3f3c25bc0b0d3cf4b90c2a3688e4eb5efbdab5de`
and exact SHA-256 hashes of service.py, serial_posix.py, and controller_receiver.py;
all three match that commit. Only the two local Pi config files are untracked.
The code identity is therefore verified despite stale advertised build metadata.

Code-derived timing flaw: PosixSerial._reader_loop holds _receive_lock across
a 100 ms select, while read_line(timeout=0) first acquires that same lock.
ControllerReceiver uses these supposedly nonblocking reads under _ingress at
admission and quiet-boundary checks. A local Linux WSL pseudoterminal probe of
the unchanged production transport measured 91.44 ms average zero-timeout reads;
the combined receiver probe exceeded its 25-second diagnostic deadline, with
the consumer waiting for _ingress and the receive owner waiting for the raw
reader's _receive_lock. A Windows queue-only fake did not model this raw-reader
lock and averaged 34.04 ms per ACK/quiet/end cycle. This demonstrates a missed
transport deadline/lock-contention defect, not proof that every observed speed
limit has that cause. No physical motion or controller command was initiated
by these probes; hardware rate/override changes and safety-check removal are
not proposed. Investigative script is ignored under build/investigate_speed.py.

Implemented Pi-side timing correction: raw readiness waiting moves outside
the queue/framing gate, and readiness is rechecked under the synchronization
gate before consuming bytes. The primary receiver registers pending consumer
work so the polling thread cannot repeatedly reacquire ingress ahead of it.
No ACK quiet interval, response ownership, STOP, Home, feed, arming, or controller
setting was changed. The same WSL probe now measures 0.04 ms zero-timeout reads
and 22.73 ms ACK cycles (44.0 lines/s). After the serial-only correction it still
measured 828.87 ms cycles, demonstrating why both cooperating layers matter.
These are local PTY timings with immediate fake ACKs, not physical Pi speeds.
See [serial receive timing](docs/SERIAL_RECEIVE_TIMING.md) for the cause, design,
regression risks, and operator validation. Windows receiver/session/remote and
shutdown checks pass 223 tests; a separate initial run passed 30 and explicitly
skipped 18 POSIX cases. Final Linux tests and deployment handoff follow below.
Keep the frozen Windows 0.6.202 bundle; this correction executes on the Pi.
The first broad Linux run passed 177 tests and failed three assertions in two
handshake fault fixtures: their 10 ms total command budget could fail at the
mandatory 10 ms quiet interval before reaching the intended fault. Those two
fixtures now allow 100 ms without changing production timing or expected fault
classification; all six affected cases pass on Windows. The relevant PTY
MachineService fixture now persists acknowledged $1 writes and asserts actual
job completion. Linux rerun results follow at handoff.
The rerun exposed the same insufficient budget in a third recovery fixture;
that test now also reaches its intended missing-$#-ACK failure before recovery.
Final Linux Python 3.10.12 POSIX/receiver/primary-event/controller-session run:
180 passed in 35.79 s, including the actual PTY transport tests. All seven
affected handshake/recovery fixture cases pass on Windows after those test-only
changes. Repository Ruff, compileall, and diff whitespace checks pass. The
existing full compatibility failures remain unresolved until the new CI run
completes; this is a Pi feature-test correction, not a main/release integration.
Physical 1500/3000 speed comparison and STOP/Home/repeat-job acceptance remain
pending. No Pi deployment or hardware motion was performed by the agent.
Pi candidate source is `c016e1978a3e3a5f16b1356e9036b26acfe24008` on
`codex/status-authority-home`; full compatibility run `33980957659` was
dispatched for that source. Guarded operator commands and the speed/STOP retest
are in ignored `build/PI_SPEED_UPDATE.md`; the extracted script passed Bash
syntax checking without execution. Windows remains the exact previously
selected frozen 0.6.202 build. The Pi configuration and paused root EAGAIN
experiment were not modified or included.

Operator acceptance follow-up for Windows 0.6.202 / `a152b50` on 2026-09-05:
reported Connect, Generate, Start, interrupt with STOP, then Generate and Start
again working without the previous error dialog. This records physical
operator-observed success for that sequence, not complete system acceptance.
The report does not specify the intervening Home sequence, so it does not
verify automatic Home on START or motion admission without Home. The hardware
is the previously reported GRBL setup; exact firmware/configuration and deployed
Pi revision were not independently re-read during this retest.

The preceding 0.6.202 PI NOT RESPONDING screen coincided with SSH reachable and
both service ports refusing connections. Operator-provided systemd output at
11:05:19 MDT showed MainPID=0, inactive/dead, Result=success, ExecMainStatus=0,
and NRestarts=0. After instructions to start the service, the operator attributed
the stopped service to their own action and reported the successful sequence
above. This connection incident is not evidence of a 0.6.202 client regression.
The full compatibility failures below still block integration/release. No
production code or frozen build changed for this acceptance record.

Operator follow-up on the 0.6.200 handoff: initially reported no problems, then
reported the requested consecutive small powered-job sequence worked. After
interrupting with STOP, homing, and requesting another job, the desktop showed
"Controller job failed: Job stopped" together with STATUS UNAVAILABLE / STATE
UNKNOWN. The operator clarified that the new job continued after the popup,
even before dismissing it. The originating Pi job UUID is not established.
Controller/firmware/configuration identity remains
the prior reported setup, not independently re-read for this observation.

Read-only follow-up investigation reproduced that DesktopController.poll_status
emits this generic failure for a stale `state=stopped` job, and can emit it twice
for the same UUID if finished_at changes. This is a demonstrated notification
classification/deduplication weakness, not proof of the physical incident's
cause. Code also marked every pending START as a lost response before sending
the request, and could reuse a previous terminal record when forming the new
starting record. Job reads lacked an independent lifecycle publication guard.

The Windows follow-up separates pending START from actual response loss,
creates fresh upload/start records, rejects job observations superseded by a
new lifecycle, and keeps raw controller diagnostics outside durable Pi job
identity. Desktop terminal errors require fresh terminal Pi records, identify
the job name/UUID, and deduplicate by UUID. Expected STOP is quiet; distinct
cleanup failures and actual failed/interrupted jobs remain visible. No Pi,
controller, Home, arming, STOP, or serial implementation changes are included.
Windows Python 3.14.4 focused remote-client, offscreen desktop job/reconnect,
shutdown, and Pi-owned protocol end-to-end tests pass: 179 tests. Final cache
freshness refinements pass all 81 remote-client/notification/shutdown tests.
Repository Ruff and compileall pass. These use fake controllers and loopback
protocols, not physical hardware; frozen build verification and operator
acceptance remain pending. Existing supported compatibility failures below
remain unresolved, so this branch is not ready to merge or release.

Follow-up frozen source: `a152b50b5c693720c4ab6892554885f2e4d43de5`, version
0.6.202, built separately under `.codex-worktrees/job-lifecycle-build` so the
running 0.6.200 bundle remains intact. The isolated offscreen frozen EXE stayed
alive for 15.29 seconds with no startup error or stderr. PyInstaller's module
manifest resolves both changed modules to this exact build worktree. Changed
Python files also parse with Python 3.10 grammar. No interactive GUI or new
physical validation was performed by the agent.

Follow-up compatibility run `33979147338`: Ruff passed. Linux passed 350 tests
and failed three: the two previously recorded $1/M84 fixtures, plus
`test_invalid_realtime_handshake_never_publishes_candidate[timeout]`, which
expected the status-timeout diagnostic but failed earlier at the quiet terminal
boundary. That controller/transport path is unchanged by this Windows follow-up;
the extra failure is not declared benign or resolved. Windows 3.10 passed 3,091
tests with 68 skips and 11 failures; Windows 3.12 passed 3,675 with 21 skips and
16 failures. Neither changed test module failed. Full-suite failures remain in
controller/serial transcripts, asynchronous motion/completion/STOP, Pi-server
integration, and secondary timeout recovery tests. They overlap prior baseline
failures but vary by run; no claim is made that every failure is harmless.
[Exact compatibility run](https://github.com/lukelave-boop/E3/actions/runs/33979147338)
remains red and blocks integration/release.

The required Windows packaging script completed successfully, including native
bundle validation and installer creation. The permanent E3 DEV TEST pointer now
selects exact 0.6.202 / `a152b50` at
`C:\Users\lukel\Documents\E3\.codex-worktrees\job-lifecycle-build\dist\E3\E3.exe`,
feature **STOP recovery and job status**. No currently running app was closed.
This follow-up is Windows-client-only; retain the previously deployed 0.6.200
Pi source. Operator retest: save/close the idle old app, reopen E3 DEV TEST,
then repeat a small job, STOP, Home/park, and a new job. Expected STOP must not
display a delayed failure for the new job; normal pending Start must not itself
mark machine status unavailable. Actual status failures remain possible and
are not hidden. Matching Pi logs are still required to attribute the reported
physical incident conclusively.

Branch `codex/status-authority-home` is isolated from `55741cc` in a separate
worktree. It includes independent machine/job observation freshness, coherent
job-record reuse, three-second machine-snapshot expiry, five-second authenticated
node-contact reporting, rejection of superseded observations and retired Pi boot
responses, bounded RPC failure diagnostics, and idle/disarmed explicit repeat
Home/park. Repeat Home invalidates reference before I/O and requires the full
held-stepper Home/park validation before READY_MOTION. STOP and controller
quarantine remain fail-closed. [Pi status authority](docs/REMOTE_STATUS_AUTHORITY.md)
records the contracts and physical validation procedure.

Prior-build operator evidence: Pi `55741cc` plus Windows 0.6.196 successfully
connected to the original Espressif Device by-id path (ttyACM1), homed/parked,
held READY_MOTION, completed successive powered circles, and stopped powered
motion/output immediately with Home required. A subsequent Home failed waiting
for `$$`; Pi status replies and service uptime continued, with contemporaneous
USB/undervoltage evidence. After removing the unrelated Espressif JTAG device,
Home succeeded but desktop PI OFFLINE flashes persisted, including with live
overlay off. These observations verify the reported prior-build sequences only;
they do not establish the cause of every timeout or acceptance of this revision.
The earlier experimental serial EAGAIN edit remains outside this worktree and
is not included. Automatic Home on START, secondary redesign, and independent
laser fan control remain separate work.

Verification: the full local run passed 3,666 tests with 24 platform skips and
five failures. Four were old queued/disabled-Home expectations updated to the
explicit idle-only contract. The final affected controller, MachineService,
remote/Pi server, and offscreen desktop run passes 552 tests. A subsequent
diagnostics-view addition passes all 33 projection/runtime-strip tests.
Repository Ruff and compileall pass. The remaining full-suite failure is the
previously recorded Marlin powered-completion transcript expecting M84; it is
unchanged here. Local runtime is Windows Python 3.14.4, not supported Windows
3.10/3.12 CI. Final build and supported CI evidence follow below; this revision's
physical acceptance remains pending. No production hardware was contacted during
this implementation. The branch is not ready to merge with compatibility red.

Final publication review additionally preserves coherent Pi job ownership in the
same machine-cache publication and retains prior durable ownership while legacy
job details refresh. The intermediate cache must never make a Pi-owned job look
locally owned or idle. Remote, Pi execution, and shutdown regression tests pass
115 cases, including two new intermediate-publication tests. Ruff/compileall
pass. The preliminary e66b1e0 Windows 0.6.199 package passed its isolated
15.18-second offscreen launch check; it is superseded by this ownership
correction and is not the operator handoff. Final frozen 0.6.200 uses exact
source 3f3c25bc0b0d3cf4b90c2a3688e4eb5efbdab5de.

Compatibility run 33975743576 tested e66b1e0: Ruff passed; Windows 3.10 reported
3,088 passed / 68 skipped / 6 failed; Windows 3.12 reported 3,664 passed / 21
skipped / 10 failed. Linux reported 343 passed / 2 failed (the older fixed-$1=250
pseudoterminal fixture and Marlin M84 expectation). Windows failures include
short fake-controller deadlines/event waits and incomplete-job assertions;
baseline run 33976246701 on deployed 55741cc is being compared before those
are classified. No green compatibility or production-readiness claim is made.

Baseline comparison completed: run 33976246701 on unchanged deployed 55741cc
reported Windows 3.10: 3,071 passed / 68 skipped / 10 failed; Windows 3.12:
3,651 passed / 21 skipped / 10 failed; Linux: 333 passed / 2 failed. The two
Linux failures are identical to e66b1e0. Windows reproduces overlapping
controller deadline/event-wait, secondary completion, and receipt/park timing
failures, with some differing failed tests across runs. This establishes an
unhealthy pre-existing CI baseline, not proof that every differing failure is
benign. Final source 3f3c25b (0.6.200) completed CI run 33976703955: Ruff passed;
Windows 3.10: 3,084 passed / 68 skipped / 12 failed; Windows 3.12: 3,667 passed /
21 skipped / 9 failed; Linux: 345 passed / 2 failed. New monitoring, repeat-Home,
and ownership tests passed. The Linux failures are the same two fixture issues;
Windows retains varying short-deadline/event/unfinished-job failures including
overlap with the unchanged baseline. Compatibility remains red, not waived.

Final build: `packaging/build_windows.ps1` completed successfully for 0.6.200 /
3f3c25b. Analysis records confirm the changed packaged modules come from this
isolated worktree. The exact frozen executable passed a 15.20-second offscreen
launch-only smoke with no early exit or startup-error report. Camera autostart
and motion were disabled, the controller endpoint was unselected, and app/data
paths were temporary. This is not an interactive GUI, camera, Pi, motion, or
laser test. Changed Python files parse with Python 3.10 grammar.

The permanent E3 DEV TEST pointer was atomically selected using
`packaging/set_dev_test_feature.py`. It selects this worktree's
`dist/E3/E3.exe`, version 0.6.200, revision 3f3c25b, feature
"Pi status authority and repeat Home". Adjacent build-info metadata matches.
The old running E3 process and the normal launcher were not replaced. The Pi
has not been updated by this implementation; operator commands and the first
idle/repeat-Home test are in `build/PI_UPDATE_AND_TEST.md` (local handoff artifact).
The script's Bash/Python syntax was checked without executing it on hardware.

## Active audit step 1: primary GRBL session authority

Branch: `codex/primary-session-authority`, based on local 0.6.196 revision
`5ebd15bf8675579ee150a99af27dc1cb7be37c66`. This source implementation adds
continuous primary receive ownership after the full private handshake. Idle
alarms, restart/framing events, and transport failures revoke exact-session
trust; queued unowned replies cannot acknowledge new commands. Admission and
write share one receive boundary. Arming, Home reference publication, and job
terminal publication respect already observed faults. STOP and quarantine use
bounded GRBL realtime abort before M5/close; normal successful jobs retain the
existing motion barriers and verified $1=255 hold. The original failure survives
cleanup and E3's own reset reply. Marlin ordinary/emergency policy is retained.

[Primary session authority](docs/PRIMARY_SESSION_AUTHORITY.md) records the scope,
GRBL untagged-ACK limitation, and physical acceptance sequence. Auto-Home/Start,
desktop/Pi snapshot design, secondary lifecycle, and independent fan control are
later audit steps. No Pi deployment or frozen feature build was made for this
step; the permanent DEV TEST pointer still selects the previous feature.

Verification: the full Windows suite completed with **3,656 passed, 24 skipped,
2 failed**. A Home/STOP cancellation-message regression was corrected afterward.
The final focused controller, receiver, machine, and GRBL transcript run passes
**397 tests**, including all 27 new receiver/event races and the 1,000-lifecycle
soak with no new surviving threads. That focused run excludes the sole unresolved,
pre-existing Marlin powered-completion transcript failure: it expects M84, which
the current implementation omits. Five stale GRBL transcript expectations were
updated to the existing held-reference contract. The final full run's other Pi,
remote, camera, desktop, and shutdown tests passed; the full suite was not repeated
after the focused cancellation correction and cleanup-test timing corrections.
Repository Ruff and compileall pass, and changed production sources parse with
Python 3.10 grammar. Runtime testing uses Windows Python 3.14.4, not the supported
Windows 3.10/3.12 CI tiers. Physical primary/Pi/laser/camera acceptance and the
Linux serial-specific checks remain unperformed. No hardware behavior is claimed
verified by these fake-controller tests or offscreen desktop tests.

## Active bounded persistent-secondary pre-start OFF recovery

Pre-start secondary OFF permits exactly one fresh-session recovery after a
persistent-session synchronization, write, acknowledgement, or framing failure.
The existing owner closes the uncertain session, reopens, settles, synchronizes,
and requires a new acknowledged `M106 S0` before primary streaming. Failure of
that sole retry preserves both bounded diagnostics and rejects Start. Air Assist
ON is never automatically replayed. Startup/restart, STOP, mapping validation,
and primary GRBL readiness/stepper-hold behavior are unchanged.

User-reported 0.6.195 physical evidence: first powered circle START accepted at
05:46:27 and completed at 05:46:37, retaining READY_MOTION. Second Start failed
on secondary OFF acknowledgement timeout without primary execution; the desktop
reported the preserved error and Pi recorded FAILED. Cleanup restored $1=250
and READY_HOME_REQUIRED. This verifies those observed prior-build behaviors.
The new recovery remains physically unverified.

Focused Windows secondary/controller integration, Pi server, and desktop shutdown
checks pass 100 tests. Repository Ruff, compileall, and diff checks pass. No full
pytest or hardware access. Frozen build/smoke verification is pending.

## Active Pi secondary pre-start OFF and Start-error correction

Pi Start now synchronizes idle secondary RX before a fresh acknowledged
`M106 S0` and uses the existing bounded framing-rejection reopen policy.
Startup/restart OFF and exact typed mappings remain unchanged. Failed Start
preserves bounded secondary diagnostics; cleanup STOP no longer manufactures an
operator STOP. Desktop rejection returns promptly and reports the error once,
including when controller cleanup invalidates its session. Physical retesting
remains required; the original physical exception was not retained, so idle RX
contamination or a framing rejection cannot be confirmed from that log alone.

User-supplied physical evidence at 51657773 records restart OFF, Home with
acknowledged `$1=255`, and READY_MOTION, followed by pre-start secondary OFF
failure and cleanup restoring `$1=250` / HOME_REQUIRED. No primary Start
transaction was recorded. This validates those observed prior-build transitions,
not this correction or successful powered execution. Motion code is unchanged.

Focused Windows Pi/remote/desktop checks pass 98 tests; secondary/integration/
desktop checks pass 48 tests (overlapping desktop selection). The focused MachineService/session/Pi/secondary/desktop selection passed
480 tests with 16 expected Windows POSIX skips before the final added cases.
Repository Ruff, compileall, and diff whitespace checks pass. Full pytest is
intentionally skipped. No physical hardware, camera, or Pi was contacted. Frozen 0.6.195 at dd2a0020acf010570144c7f91c8ce7a95ed19ab9 passed
the isolated offscreen 15.29-second launch-only smoke without early exit or
startup-error reports. Camera autostart and motion were disabled, the controller
endpoint was unselected, and user data paths were temporary. This was not an
interactive GUI or physical test. The permanent E3 DEV TEST pointer selects
that exact bundle. Operator verification remains pending.

## Active GRBL motion-readiness stepper-hold correction

`READY_MOTION` now requires a Home-established coordinate reference for the exact
controller generation and a controller-verified continuous GRBL stepper hold
(`$1=255`). Normal successful jobs keep that held reference, including after the
configured powered-job Home / park completion, so another job may start without
Home while the same trusted held session remains valid. Intentional motor release,
STOP, fault, quarantine, reconnect, restart, or uncertain controller communication
invalidates the reference and requires Home. No `$SLP` or `$MD` command is used.
This correction has automated verification only and remains physically unverified.

## Prior GRBL post-job motor-release correction

Physical validation established that a powered job and its automatic Home / park
completed successfully, after which this controller rejected `$MD` with `error:2`,
accepted `$SLP`, reported `[MSG:Sleeping]`, and stopped answering fresh `$I`
handshakes. GRBL cleanup now restores the configured validated finite `$1` value
and relies on normal GRBL step-idle release behavior. It does not send `$MD` or
use controller sleep merely to release motors, so unsupported nonessential
release extensions cannot turn a completed powered job and successful Home / park
into a generic controller-job failure. M5, Home / park, coordinate invalidation,
STOP, session quarantine/reconnect behavior, Air Assist, and ESP-IDF diagnostic
handling are unchanged. The correction remains physically unverified.
Focused Windows verification passes **409 tests** across MachineService, GRBL
dialect, and controller-session coverage. Repository Ruff, `compileall -q
laser_aligner`, and `git diff --check` pass. The complete repository pytest suite
was intentionally not run for this narrow correction. No Pi, controller, laser,
motion hardware, Air Assist hardware, camera, or serial endpoint was accessed.

## Active ESP-IDF diagnostic / GRBL serial multiplexing correction

Physical validation established that the primary ESP32 controller can emit a
well-formed ESP-IDF `E (...) tag: message` diagnostic, including ANSI color
framing, on the same serial stream while `$H` is active. The pre-correction
parser treated the observed `gpio_isr_handler_remove(480)` diagnostic as a
malformed GRBL homing frame, correctly quarantined the ambiguous session, and
successfully recovered on a fresh generation to `READY_HOME_REQUIRED` twice.
That recovery behavior is retained; this is physical evidence of the controller
output and prior compatibility defect, not physical validation of this correction.

GRBL receive handling now conservatively classifies bounded ESP-IDF
`E`/`W`/`I`/`D`/`V` log frames as `firmware_diagnostic`. Optional ANSI CSI
framing is removed only for classification and operator diagnostics. Recognized
frames remain in the bounded, generation- and transaction-labelled transcript,
but are neither payload nor acknowledgement, supply no homing-state evidence,
and cannot satisfy or extend a transaction deadline. Arbitrary `E` text,
`error:x`, `ALARM:x`, malformed frames, and realtime Home/Idle states retain
their authoritative behavior. Home still requires its existing terminal `ok`
or verified active-to-Idle fallback before coordinate, mode, optional park,
planner, and final-position validation can publish `READY_MOTION`.

Focused Windows controller-dialect, session, machine, transcript, and Pi-server
verification passes **467 tests**. The complete Windows four-worker suite passes
**3,608 tests with 24 expected platform or privilege skips**. No Pi, network service, serial endpoint,
controller, motion, arming, laser, Air Assist, camera, or other physical hardware
was accessed for the correction. The corrected build remains physically unverified.

## Active explicit-GRBL identity-payload compatibility correction

Physical diagnostics from the configured Pi/controller combination established that
six fresh serial generations completed `$I` with a clean `ok` in 0.012530–0.013399
seconds but returned no optional `[VER:]`, `[OPT:]`, or other identity payload. The
pre-correction handshake rejected each generation as an identity mismatch even though
the Pi configuration explicitly selected `machine.protocol = grbl`. This is physical
evidence of the controller's `$I` behavior and the prior compatibility defect; it is
not physical validation of the corrected build.

For an explicitly configured GRBL session, a clean acknowledgement-only `$I` now
records that identity payload is unavailable and proceeds to the existing complete
GRBL capability verification. It does not treat the acknowledgement as proof of GRBL.
Publication as `READY_HOME_REQUIRED` still requires acknowledged `M5`, a valid `$$`
response and required `$1` validation/repair, valid `$G` and `$#` reports, and a valid
realtime `?` status frame. Auto/unspecified protocol still requires positive identity,
contradictory positive identity still fails closed, and malformed or incompatible
later capability responses now report `controller.handshake_incompatible` rather than
an identity/configuration error.

Focused Windows controller-session verification passes **124 tests**, including
acknowledgement-only and normal `$I` success, malformed `$$`/`$G`/`$#`/`?` rejection,
auto-detection refusal, contradictory identity, stale-generation, STOP, reconnect, and
transaction-ownership cases. The wider controller-dialect, Pi-server, and remote-service
selection passes **264 tests**. The complete Windows four-worker suite passes **3,603
tests with 24 expected platform or privilege skips**. Repository Ruff, `compileall -q
laser_aligner` with an external bytecode cache, and `git diff --check` pass. No Pi,
serial endpoint, controller, motion, arming, laser, Air Assist, camera, or other physical
hardware was accessed for the correction.

## Active Windows frozen-startup hardening

The Windows build now sanitizes its PyInstaller environment before dependency
analysis and rejects the known conflicting root-level ICU/OpenSSL libraries in
the completed bundle. This prevents tools on the invoking process `PATH` from contributing
incompatible native libraries. The GRBL-hardening build at revision `a30f2e5`
failed before Qt startup because PyInstaller collected ICU 78 from the bundled
Codex/Poppler runtime; Qt 6 then loaded that `icuuc.dll` instead of the compatible
Windows system library and `PySide6.QtCore` raised `ImportError: DLL load failed
while importing QtCore: The specified procedure could not be found.`

Frozen startup import failures now preserve the original exception and write a
bounded diagnostic report to the per-user `logs/startup-error.log`, then show a
native Windows error dialog that directs the operator to rebuild or reinstall
the package. Source checkouts retain the existing PySide6 installation guidance.
Focused startup, build-guard, source, and recovery verification passes 26 tests.
The complete serial Windows suite passes **3,595 tests with 24 expected platform
or privilege skips** and has one unrelated failure in the unchanged desktop-menu
test: its `QMenu` wrapper is already deleted under the local PySide6 6.11.1
runtime, both in the full suite and when run alone. Repository source-directory
Ruff, `compileall -q laser_aligner` with an external bytecode cache, PowerShell
syntax parsing, and `git diff --check` pass.
No Pi, network service, serial device, controller, motion, arming, laser output,
or physical hardware was accessed while diagnosing or testing this correction.

## Active primary Raspberry-Pi-to-GRBL session hardening

The primary controller path now uses one explicit, generation-bound controller
session state machine across the Windows desktop, Pi RPC boundary, Pi-owned
`MachineService`, and POSIX serial transport. A connection is published only
after receive synchronization, controller identity, fail-off, settings/modal
checks, and realtime-state validation. Recovery always creates a fresh transport,
can publish only **Home required**, never homes or resumes automatically, and
keeps stale workers and cleanup bound to their original transport and generation.

STOP revokes job and arming authority before bounded exact-session fail-off and
close. Home, arm, Start, diagnostics, UI actions, and Pi mutations use explicit
state plus session/boot/client compare-and-swap metadata. The desktop has one
authoritative projection for labels, action enablement, and remediation. The Pi
job journal records terminal failures without retrying or resuming accepted work.
The POSIX owner now uses process locking, `TIOCEXCL` when available, strict UTF-8
framing, synchronized reopen, and descriptor-stable reads/writes.

The 60-case fault matrix is indexed in `docs/GRBL_SESSION_FAULT_MATRIX.md` and
includes deterministic races for reconnect while an old job worker unwinds,
stale cleanup attempting to close a replacement, and stale work attempting to
write it. Focused Windows verification passes **220 MachineService tests**,
**117 controller-session tests**, **156 Pi/RPC tests**, **28 desktop state and
reconnect tests**, and **22 strict transcript tests**. The complete Windows
four-worker suite passes **3,587 tests with 24 expected platform or privilege
skips**. Repository Ruff, `compileall -q laser_aligner` (with an external bytecode
cache), and `git diff --check` pass.

Windows cannot execute the 16 POSIX pseudoterminal cases. Ubuntu 24.04 Python
3.12 Fast Development CI run `33827402790` passes all **305 focused POSIX serial
and controller-session recovery tests** after correcting RX-lock starvation; its
Ruff and dependency/bytecode jobs also pass. No Pi, network service, real serial
device, controller, motion, arming, laser output, or physical STOP/recovery cycle
was accessed. Physical verification remains explicitly pending under
`docs/GRBL_SESSION_RECOVERY_VALIDATION.md`; software controls are not safety-rated.

## Active Pi secondary Air Assist serial framing correction

The Pi-local POSIX serial transport now owns an explicit pre-command receive
synchronization operation. After the secondary controller's startup settle
delay, it discards queued complete lines, any unterminated receive fragment,
and unread kernel RX bytes with `TCIFLUSH` before sending the unchanged exact
`M106 S0` initialization handshake. Receive framing, draining,
synchronization, close, and reopen share receive/lifecycle locking so data from
an old descriptor cannot acknowledge a command in a new logical session.
Output data is not flushed.

An `Unknown command` rejection during the initialization handshake closes the
untrusted session and permits exactly one reopen, settle, synchronize, and
`M106 S0` retry. A second rejection remains fail closed. Durable recovery still
uses the exact binding accepted with the prior job and is cleared only after an
acknowledged OFF; disabling the current profile affects only future jobs.

Focused Windows verification passes **84 secondary-controller, Air Assist,
Pi-owned lifecycle, recovery-store, and remote-node tests**. Targeted Ruff,
`compileall -q laser_aligner`, and `git diff --check` pass. The complete Windows
four-worker suite passes **3,418 tests with 19 expected platform or privilege
skips**. The new POSIX
pseudoterminal regression covers complete CR/LF/CRLF startup lines, invalid
UTF-8 and the exact unterminated `\x13\xfaBAD` fragment, plus bytes pending in
kernel RX, but remains unexecuted on Windows because POSIX pseudoterminals and
`termios` are unavailable; the available local WSL Python lacks pytest. No Pi,
network service, real serial port, controller, fan, motion, arming, or laser was
accessed. No new physical verification is claimed.

## Final feature integration cleanup

The authoritative independent Trace hole-area implementation, Camera Trace
Outer silhouette mode, and raster-native primitive recovery are consolidated on
`integration/final-feature-cleanup` from `origin/main` revision
`e0b0223d62d845c3297c3702bce75bf116f8658e`. Conflict reconciliation preserves
cooperative cancellation, independent inclusive object/hole limits, protected
background semantics, Full-detail and exterior-only topology, primitive
recovery diagnostics/fallback, native fitting, and the newer guided calibration,
Pi execution-policy, shutdown, Air Assist, STOP, profile, updater, and DEV TEST
behavior already on main.

Focused combined verification passes **319 Trace/raster/native/primitive tests**,
**210 calibration, setup, remediation, honeycomb, and profile tests**, and **147
Pi policy/execution, Air Assist, shutdown, STOP, updater, and identity tests**.
The complete Windows four-worker suite passes **3,416 tests** with **15 expected
platform or privilege skips**. Repository Ruff, `compileall -q laser_aligner`,
and `git diff --check` pass.

`feature/s1pro-z-homing-safety` remains intentionally deferred. Its isolated
implementation and focused tests are complete, but the branch predates Pi-owned
secondary Air Assist and creates a separate persistent serial owner for the same
Creality controller. Current architecture requires Z and Air Assist to share the
single `CrealityControllerOwner`; merging the branch unchanged would create
competing command paths. The branch is retained until that shared-owner design
and its focused concurrency, STOP, failure, and recovery tests are implemented.
No new physical hardware verification is claimed by this integration.

## Active Python 3.10 post-merge compatibility correction

Post-merge Windows CI run `33586179523` failed only because the test subprocess
for invalid E3 DEV TEST pointer installation exceeded its 30-second ceiling on
the Python 3.10 runner. The preceding PR-head Python 3.10 job passed, the PR-head
and merge revisions have the identical Git tree, and the exact affected test
passes locally under Python 3.10.20 in under one second. The test retains its
exact fail-closed rejection and no-write assertions while allowing 120 seconds
for cold Windows PowerShell/Python startup and endpoint scanning on a loaded CI
host. Production launcher behavior and all machine, calibration, shutdown,
Air Assist, STOP, and safety authority are unchanged.

The exact affected Python 3.10 test passes in **0.88 seconds**. The complete
CI-equivalent Python 3.10 base/non-desktop suite passes **2,716 tests with 62
expected skips** in **143.16 seconds**, and the complete Python 3.12 desktop
suite passes **3,270 tests with 15 expected platform or privilege skips** in
**194.76 seconds**. Repository Ruff, `compileall -q laser_aligner`, and
`git diff --check` pass.

## Active guided calibration and actionable preflight remediation

Machine Setup now places a compact state-driven **Goal / Do this now / Done
when** guide at the top of all six numbered tabs. Bed Mapping presents one
unified **Honeycomb frame** workflow: **1. Home, park & capture ruler overlay**
followed by **2. Detect & save honeycomb frame**. The ruler overlay and saved
frame report independent MISSING/CURRENT/STALE state. The second action stays
disabled until the active bed map owns a current ruler capture, successful
capture visibly identifies the next action, and the full-width controls retain
their complete text at the minimum dialog width through tested 100%, 125%,
150%, and 200% font-scale equivalents. **Clear ruler preview** changes only the
transient displayed capture. Explicit saved-frame removal and the
diagnostic-only three-hint fallback live under **Advanced / troubleshooting**.

Automatic four-edge review now offers **Save honeycomb frame**, **Try again**,
and **Cancel**. Only Save calls the existing persistence path; retry and cancel
clear the candidate without making it current. A saved automatic frame becomes
CURRENT immediately and remains CURRENT after restart when its bed map,
configured span, teaching-image bytes/metadata, and support-frame bindings are
unchanged. Invalid saved-reference files and stale teaching bindings now expose
stable reason codes and their exact technical reason without decoding image
pixels or changing execution authority.

Qt-neutral `PreflightFinding` values carry immutable ordered resolution steps
and stable navigation target/label strings. Calibration and coordinate blockers
select camera, lens, base-map, physical-span, saved-frame, work-area, guarded-
polygon, or profile-binding recovery from structured reason codes rather than
display prose. The desktop shows a numbered **How to fix** section, retains the
technical reason, dismisses the blocked report, and maps navigation through a
fixed allowlist to the relevant Setup tab or Machine Manager field. Navigation
performs no Home/park, capture, motion, calibration write, arming, or laser
action. Navigation-only Machine Setup opens also suppress automatic pending
lens-evidence indexing until the operator explicitly chooses an evidence
action. Saving calibration evidence invalidates stale prepared jobs and
preflight reports through the existing `calibrationChanged` path.

The guided-calibration build at revision `097b8fdd654233e096dfe99aae3fe94105f16373`
was physically/UI validated by the operator before synchronization. The clearer
numbered Bed Mapping flow, ruler overlay, automatic detect/save flow, explicit
**Save honeycomb frame** dialog, immediate CURRENT state, persistence of CURRENT
after restart, and minimum-dialog button layout were accepted. That campaign did
not require repetition when the already validated feature was merged forward.

After merging authoritative main revision
`d409ead3938435ad380ada87a79821f1181eb3b7`, focused calibration, persistence,
preflight, navigation, Pi-policy diagnostic, and desktop startup/shutdown
verification passes **404 tests** in **191.07 seconds**. The complete Windows
four-worker repository suite passes **3,270 tests** with **15 expected platform
or privilege skips** in **241.61 seconds**. Repository Ruff,
`compileall -q laser_aligner`, and `git diff --check` pass. The synchronization
adds no new physical behavior claim beyond the operator evidence above and the
separately recorded later Pi-policy physical verification. Calibration
mathematics, acceptance thresholds, detection mathematics, guarded output
limits, machine authority, motion behavior, STOP, shutdown, and laser/Air Assist
authority remain unchanged; the software guidance is not safety-rated.
## Permanent Windows feature-test launcher

The pointer-driven **E3 DEV TEST** launcher is implemented and installed at
`C:\Users\lukel\Documents\E3 Dev Test\E3 DEV TEST.exe`, with its validated
pointer beside it and a dedicated `C:\Users\lukel\Desktop\E3 DEV TEST.lnk`.
The launcher and shortcut use the explicit `E3.DevTest` AppUserModelID and a
separate orange DEV icon. The normal E3 executable, implicit process identity,
icon, and Desktop shortcut remain unchanged. The normal shortcut SHA-256 stayed
`657FC4B36FC34BF0562E793A448637BD2B37949D8187C3B473040535F325E839`
before and after installation.

The permanent launcher is a one-file Windows GUI-subsystem executable (PE
subsystem 2), is 9,553,388 bytes, and has SHA-256
`1828B10B2A96E9A032428E650C463D9AB9104BBF4739ED3F05BF3CAF7677232B`.
It strictly accepts exactly the five documented pointer fields, binds version
and revision to adjacent packaged Windows build metadata, starts only the
selected absolute EXE through a sanitized external-process boundary, and never
falls back to production. Pointer installation and later selection use the same
validator and atomic writer. The Windows updater boundary strips every
`E3_DEV_TEST*` variable so a production restart cannot inherit DEV identity.

The initial selected feature is **Outer silhouette**, version `0.6.161`, branch
`feature/trace-outer-silhouette`, exact frozen revision
`45f428ac547f039988afbb3da2a0dd0f0e2d707a`, at
`C:\Users\lukel\Documents\E3\.codex-worktrees\trace-outer-silhouette\dist\E3\E3.exe`.
That EXE is 8,730,791 bytes with SHA-256
`514B7D6A9EA03362066FAB24ABE638183790BC27DAE503142BADA5BF4C8959A5`;
its adjacent schema-1 metadata records the same version and revision. The final
bundle was rebuilt with the Codex Poppler directory removed inside the Python
build process and contains zero `icu*.dll` files.

Focused Windows automation passes **61 tests** across launcher validation,
identity, desktop sources/icons, and update launching. Repository Ruff on every
touched Python file, `compileall -q laser_aligner packaging`, and
`git diff --check` pass. Interactive Windows process checks established all of
the following:

- the permanent launcher starts the configured frozen EXE, which remains live
  with title `E3 DEV TEST — Outer silhouette — v0.6.161 — Untitled`;
- a normal launch of that same bundle remains live beside it with title
  `E3 Positioning System 0.6.161 · build 45f428ac — Untitled` and is not closed
  or replaced;
- neither launch creates a new `conhost.exe` process;
- the live 32-pixel window icons differ in 980 of 1,024 pixels, with 266 orange
  DEV pixels versus 4 in the normal icon;
- the DEV environment reaches the pre-Qt AppUserModelID assignment and a live
  Qt window, while the matching shortcut property is exactly `E3.DevTest`;
- a copied launcher with no pointer shows the native
  `E3 DEV TEST — Launch failed` dialog beginning
  `No valid current feature build is configured` and names the missing fixed
  pointer path; and
- changing only a temporary `current-feature.json` selected two different
  frozen GUI probe paths in sequence while the launcher SHA-256 remained
  unchanged.

Taskbar pinning was deliberately not automated. The permanent executable and
matching shortcut are ready for the operator to pin, but an actual persistent
pin/relaunch cycle remains a user action. The live smoke used an isolated local
profile with camera autostart disabled and every saved machine's
`allow_motion` forced false. No Pi, camera, controller, motion, arming, laser
output, job streaming, or physical hardware behavior was exercised or verified;
the Pi was unavailable, and no connection was attempted. These identity and
launcher controls are not safety-rated.
## Active Camera Trace Full detail / Outer silhouette selection

Camera Trace now exposes **Trace detail** immediately above **Purpose**, with
**Full detail** as the backward-compatible default and **Outer silhouette** as
an explicit operator choice. Full detail retains the synchronized hierarchy
behavior: an exterior plus holes, islands, and deeper descendants form one
indivisible review candidate. Outer silhouette emits only the true external
closed boundary of each disconnected retained foreground root. It does not use
a convex hull, bounding box, morphological closing, gap inference, or component
merging, so an exterior-connected notch or U-shaped opening remains part of the
output boundary and disconnected roots remain separate candidates.

The source-neutral `RasterContourOutput.OUTER_ONLY` route is strengthened into
a genuine exterior-only pipeline. Preview, exact, and native-forest extraction
use bounded `RETR_EXTERNAL` work when Outer silhouette is requested, rather than
building a `RETR_TREE` and discarding children afterward. Ignored internal
contours therefore do not consume contour, raw-point, fitted-segment, fitting,
or topology budgets and cannot veto a valid exterior. The exterior itself still
passes the unchanged closedness, finite-coordinate, work-area, source-edge,
fitting-tolerance, ambiguity, and native-complexity checks. Full detail continues
to use its existing `RETR_TREE` hierarchy and limits.

The exact cleaned binary **Mask** remains immutable evidence after eligibility,
thresholding, foreground-component cleanup, and independent hole-area cleanup.
Trace detail changes only which Mask boundaries become vector geometry. Surviving
enclosed holes may therefore remain visible in Mask while Outer silhouette's
candidate overlay and native result contain one exterior subpath. The hole Min/
Max settings are retained and never overwritten. Object Min/Max area and Min
width/height retain their numeric values; Full detail's post-fit area remains its
legacy even-odd candidate area, while Outer silhouette's post-fit area describes
the emitted exterior silhouette.

Manual and bounded-Auto **By contrast** support both detail modes through the
shared raster vectorizer. Top-level Auto propagates Outer silhouette through its
dark-raster, light-raster, and Color attempts. Explicit/Auto Color uses external
root extraction in Outer mode and deliberately skips washer recognition because
that classifier enumerates child contours; thresholding and color matching are
unchanged. Grid keeps its specialized Full-detail behavior: the Trace-detail
control is disabled while Grid is active, its effective option is Full, and the
operator's saved selection is restored when Grid is disabled.

Trace detail is a QSettings preference, not a project-schema field. Missing or
invalid stored preferences resolve to Full detail. Only objects created from an
Outer silhouette result receive `trace_detail = outer_silhouette` provenance;
legacy Full/Grid object metadata remains byte-for-byte compatible. Extraction,
root processing, native fitting, result publication, and Create retain cooperative
cancellation, including rejection of late cancelled Outer results. Diagnostics
are bounded and record the detail mode, external/output root counts, area basis,
whether child contours were enumerated, and the exterior/root-filter/work-area/
complexity failure stage without inventing ignored-child failures.

Windows automated verification passes **397 non-overlapping focused Trace,
silhouette, hierarchy, Mask, preference, overlay, Create, and cancellation tests**
(264 UI/Trace tests in **231.22 seconds** plus 133 core raster tests in **23.36
seconds**). The bounded desktop-shutdown regression passes **514 tests** with
**2 expected Windows skips** in **61.60 seconds**. The complete four-worker
repository suite passes **3,253 tests** with **15 expected platform/privilege
skips** in **148.36 seconds**. These are deterministic Qt-free/offscreen tests,
not physical camera, controller, motion, or laser validation.

Windows development packaging completed from verified implementation commit
`dcef18e31ab099d6c4b402eea70b58d64b284394` as version `0.6.161` using
Python 3.14.4, OpenCV 4.14.0, PySide6 6.11.2, PyInstaller 6.22.2, and Inno
Setup 6.7.3. The frozen `build-info.json` records that exact revision. The
private machine-seeded `E3-Trace-Outer-Silhouette-Setup.exe` installer is
250,696,523 bytes with SHA-256
`B0DE921A1E16A4EC9298BD1AD3EFD37E5DA220F7A68AC904559437DC8AAB9A50`.
Four unrelated Codex-runtime Poppler DLLs discovered through the build host were
identified by their recorded source paths, removed from the generated bundle,
and excluded by recompiling the installer; the final bundle contains no
`icu*.dll`, `libcrypto-3-x64.dll`, or `libssl-3-x64.dll`. A hidden launch with
isolated temporary user state remained alive for the full 12-second smoke window
without early failure, after which the test process and temporary state were
removed. This is a frozen launch-only smoke, not an interactive GUI, physical
camera, controller, or Close-timing test.

Physical validation remains pending. The first fresh reflective-wrench A/B run
should use **By contrast**, **Auto threshold** (the recent physical scene chose
approximately 195), **Minimum area 50 mm²**, **Maximum area 8,000 mm²**,
**Minimum hole area 500 mm²**, **Maximum hole area No maximum**, and **Outer
silhouette**, then compare Full detail on the same fresh capture. Expected Outer
behavior is the same cleaned Mask evidence, one separately numbered wrench
candidate following the external profile, no enclosed-reflection subpaths, and
a usable exterior native vector. Software controls are not safety-rated.
## Active independent Camera Trace hole-area filters

Non-grid Camera Trace Contrast and Auto's dark/light raster attempts now separate
foreground-object review from enclosed-hole cleanup. The Trace Object filters are
ordered **Minimum area**, **Maximum area**, **Minimum hole area**, **Maximum hole
area**, **Minimum width**, and **Minimum height**, all area values use mm², and the
hole tooltips describe enclosed holes rather than object size. Hole controls are
available for non-grid Auto/Contrast and inactive for Grid and explicit Color.

Minimum object area first removes source-resolution connected foreground
components, including nested foreground islands. Maximum object area later
rejects complete post-vector root candidates above its inclusive limit.
Independently, enclosed background components below the minimum hole area or
above the optional maximum hole area are filled; holes exactly on either bound
or between them are preserved. `None` represents no maximum. The external
border-connected background is never filled. Background connected to hard-
ineligible pixels is equally protected and excluded from filterable-hole counts.
Cleanup changes the exact production Mask before detail-selected contour
extraction and native fitting. Full detail uses the established 4× `RETR_TREE`
hierarchy, so preserved holes retain normal descendants and deliberately filled
holes may absorb nested foreground islands into the parent. Outer silhouette
uses only external contours from that same cleaned Mask. Maximum hole area never
acts as a root/object filter.

`geometry.foreground.clean_foreground_components` remains backward compatible
while accepting independent minimum and maximum hole areas. Its detailed companion
returns bounded counts for raw, preserved, below-minimum-filled, and above-maximum-
filled holes. `RasterVectorizationOptions`, mask previews, quick/exact pixel and
asset results, metadata, Camera Trace results, and Auto raster-attempt diagnostics
carry the effective physical range and aggregate counts without retaining a
per-hole list. Existing source-neutral callers that omit the new limits still use
minimum feature area as minimum hole area and no maximum, preserving imported-
raster defaults.

Legacy Trace options and QSettings with no hole fields migrate once: minimum hole
area copies the saved minimum object area and maximum hole area becomes **No
maximum**. Persisted values are explicit thereafter, so later object-area edits do
not recouple them. Validation rejects non-finite, negative, inverted, or out-of-
widget-bound ranges rather than swapping or clamping them.

Windows Python 3.12.13 verification on the synchronized integration worktree
passes **349 focused Trace tests** in **67.07 seconds** and **512 bounded-shutdown
tests** with **2 expected Windows skips** in **51.95 seconds**. The complete
four-worker repository suite passes **3,225 tests** with **15 expected
platform/privilege skips** in **193.41 seconds**. Repository Ruff,
`python -m compileall -q laser_aligner`, and `git diff --check` pass; the diff
check reports only Git's existing LF-to-CRLF notices. Focused regressions prove
that cancellation during source-mask hole cleanup or later native fitting
publishes no accepted Trace result, and that both the controller and main-window
guards prevent a late cancelled result from publishing or creating project
geometry. This is deterministic Qt-free/offscreen automated verification, not an
interactive camera test.

Windows development packaging completed from synchronized implementation commit
`0f237489ac73823c08cbe3c5ae92b49ed8201dc9` as version `0.6.159`. The frozen
bundle's `build-info.json` records that exact development revision, the generated
`E3-Trace-Hole-Area-Setup.exe` installer is 215,121,993 bytes with SHA-256
`F7159E441CDD40E64C041604F0DCEEA7016788DA4A4CEFBBF9316B5DD2F85B2B`, and the
bundle contains no `icu*.dll` files. A hidden launch with isolated temporary user
state remained alive for the full 12-second smoke window without early failure.
Hidden mode exposed no main-window handle, so the process was stopped after this
launch-only smoke; this is not an interactive GUI or frozen Close-timing test.

This feature does not change primitive recovery, Straighten, camera
normalization, exposed-bed suppression, the Auto threshold-selection algorithm,
Grid/Color detection, project schema, planning, motion, Air Assist, Pi execution,
homing, arming, or laser-output authority. The synthetic mask and offscreen tests
do not constitute physical validation; the reflective wrench still requires a
fresh recorded camera run. The recommended first settings are **Minimum area 50
mm²**, **Maximum area 8,000 mm²**, **Minimum hole area 500 mm²**, and **Maximum
hole area No maximum**.
Exercise a finite maximum-hole value once before returning it to **No maximum**.

## Active bounded desktop shutdown correction

Accepted desktop Close now starts one monotonic four-second shutdown deadline.
The window arms a process-exit watchdog at the moment Close is accepted, before
state persistence or runtime teardown, so a non-cooperative Qt worker cannot keep
the E3 process alive past the deadline. This watchdog is an exit-only fallback;
the normal path cancels work, drains the desktop pool for at most one second,
stops the runtime under the same absolute deadline, and exits through Qt in the
ordinary way. `QThreadPool.waitForDone(-1)` is no longer used.

`RemoteCameraService` tracks every connecting or active request socket under a
lock. Address resolution runs through a daemon helper while the request worker
polls the shutdown generation, so even blocked platform DNS cannot retain a Qt
worker. Desktop shutdown latches cancellation and closes tracked sockets, waking
blocked fresh-frame, precision-burst, control/snapshot, send, and receive work.
The ordinary operation-grade camera timeouts are unchanged. Retained and labeled
`FunctionTask` wrappers remain alive until their runnables return, but their
success, failure, image, result, and busy-state publication is suppressed once
shutdown begins. Camera Trace, native fitting, raster conversion, and toolpath
generation receive cooperative cancellation checks through their CPU-heavy
loops.

Remote machine shutdown is separate from ordinary Disconnect. Only freshly
observed idle state permits one best-effort `machine.disconnect` attempt; an
empty or stale observer cache detaches without assuming the Pi is idle. Address
resolution, connect, authentication, capability check, and RPC share a maximum
0.75-second shutdown allowance; failure logs and detaches without retrying under
the normal 130-second operation timeout. An accepted or
ownership-uncertain Pi job detaches immediately and sends no RPC, STOP, `M5`,
reset, hold, Air Assist OFF, or controller Disconnect. The recent idle
Disconnect generation correction and all ordinary operation timeouts remain
unchanged.

Automated focused coverage reproduces blocked 36-second fresh-frame,
precision-burst, and control/snapshot calls; blocked address resolution and
socket creation/connect/send/receive races; an unreachable idle Pi, stale Pi
observer state, and slow-drip machine protocol; the combined blocked-camera plus unreachable-Pi
case under one shared deadline; stuck Qt-pool work in a subprocess; an updater
launch preceding blocked close preparation; late desktop callbacks; CPU
cancellation; stale/late Pi monitor races; and accepted/uncertain Pi-job
non-destructive detach. The final Windows Python suite passes **3,170 tests**
with **15 expected platform skips** in four-worker execution; repository Ruff,
`compileall -q laser_aligner`, and `git diff --check` pass.

Windows development packaging completed from shutdown implementation commit
`9687373aedee893c01d3cc6f9fb4efb15fa276a9` as version `0.6.156`. The final
windowed bundle and private machine-seeded development installer were rebuilt
with the unrelated Codex Poppler directory removed from the build process
`PATH`; the bundle contains no foreign `icu*.dll` files and launches
successfully. On 2026-08-31, accepted main-window `WM_CLOSE` to actual process
termination was measured on that frozen build with the configured hardware
profile as follows:

- ordinary disconnected state, with the camera and machine services offline:
  **279.9 ms**;
- 50 ms after invoking the exact **Refresh camera** control against the
  unreachable camera: **222.0 ms**;
- 50 ms after opening Trace and invoking its exact **Detect objects** control:
  **230.4 ms**;
- 50 ms after invoking **Connect machine** against the unreachable Pi service:
  **226.5 ms**; and
- combined camera Refresh plus unreachable-Pi Connect: **316.0 ms**.

Every performed frozen-build case was below 5 seconds and below the normal
two-second target. The Pi host answered ICMP in 7-8 ms and exposed its expected
Raspberry Pi MAC address, but TCP ports 8765 and 8766 remained closed throughout
the acceptance window. A live-camera-enabled close and an idle Pi-reachable
Disconnect therefore remain physically pending. The Trace control path was
started, but no live-image native-fit workload could be established while the
camera service was unavailable; its CPU cancellation evidence remains
automated. No physical controller motion, arming, laser output, accepted Pi job,
or Air Assist action was performed, and the software shutdown controls are not
safety-rated.

## Active physical Pi execution-policy mismatch correction

The physical **Home and start job** failure reported on 2026-09-01 is isolated
to execution-policy schema drift. The selected Windows feature build is
**Guided calibration remediation**, version `0.6.162`, revision
`097b8fdd654233e096dfe99aae3fe94105f16373`, whose base includes the current
25-field execution profile. Its saved active machine normalizes Air Assist to a
trailing `None`. An authenticated, controller-inert Pi-owned job probe using
only `G21`, `G90`, and `M5` was finalized but never started: the Pi rejected the
current 25-field digest
`ec4dbd1d5e30068ae126ea74034b93398e58b2d64d25e7f211d92a24d14f141f`
and accepted the corresponding legacy 24-field digest
`91b593c46e5b09814cd168b9fb661e4769f885265237d33043959b3406846c4f`.
Both temporary job records were deleted.

Because the Pi independently re-preflights the exact uploaded bytes before
FINALIZE, that acceptance proves all original normalized policy fields match:
backend and protocol; process and motion gates; homing behavior; work-area
bounds; laser margin, offsets, and power ceiling; feed ceilings; arming timeout;
photo position; configured guarded polygon; and the probe's absent job polygon.
The exact difference is structural: Windows has field 25
`air_assist.mapping = None`, while the running Pi has no field 25. Authenticated
capabilities and status independently show the pre-Air-Assist node surface. The
failure occurs before controller connection, Home, motion, arming, laser output,
or Air Assist output.

Authenticated shell audit then verified the exact installed unit as
`e3-hardware-node.service`, with `WorkingDirectory` and imported source under
`/home/greenhouse-climate/Projects/laser-camera-aligner`, production Python at
`.venv/bin/python`, and config
`/home/greenhouse-climate/Projects/laser-camera-aligner/config/pi-hardware.json`.
The pre-update checkout was tracked-clean `main` at
`ac9e123b1a4038e00a1ed19a380354b5aa0aab89`; its only untracked files were that
config and its existing `.bak`. The service process had started after that
commit and imported directly from the same checkout. The raw and typed Pi
configuration had no Air Assist field, and its exact 24-field runtime profile
matched the inert probe result. The bridge credential was checked only for
valid presence/length and was not printed.

The active Windows saved machine was also stale relative to the documented rig
intent: it had Air Assist disabled, while the intended matched mapping is
`secondary_marlin_fan` at 115200 baud on Pi-local endpoint
`/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0`, with the primary controller
remaining GRBL. That configuration difference did not cause the reported
24-versus-25-field rejection.

With authenticated `job.active = null`, the Pi config was copied byte-for-byte
to `/var/backups/e3/pi-hardware.json.20260902T021345Z`, the complete 34 MB Pi
data directory was archived as
`/var/backups/e3/data.20260902T021345Z.tar.gz`, and recovery branch
`pi-preupdate-20260902T021345Z` was created. The data archive is 33 MB with
SHA-256
`50dac0919750fe60514b26fa84465798e64e65ee83bb09d2752aaa62086825ed`.
The service was stopped, the clean checkout was fast-forwarded to authoritative
`origin/main` revision `8ce92ee57454afe36dac8a27ce0485ede215eeae`, and no
dependency manifest changed. `pip check` and `compileall -q laser_aligner`
passed; the production venv intentionally has no pytest, so no development
packages were installed merely to run Pi-local automation.

While stopped, the Pi config received only the intended four-field Air Assist
mapping. Both serial-by-id endpoints resolved (`ttyACM0` primary, `ttyUSB0`
secondary). E3 DEV TEST Machine Manager then saved the identical mapping through
the supported Windows saved-machine path, and the exact v0.6.162 build was
relaunched after that save. Windows and Pi now independently normalize the same
secondary profile, including `M106 S255`, `M106 S0`, and mapping digest
`e5f4015545f71910e71441b0e702fd685bf6a6142398dc3069813ffd98a4292f`.
Their no-job-polygon execution-policy digests both equal
`990313a9ca97ac580a8a4f171b26c01b7685a5ea4be8a157984c9c9cd936c424`;
with the authoritative configured polygon also used as job authority, both
equal `743d88d293df873eb98dc281f063070baf2d6200dce56bf2903d15e821417387`.

The current correction branch adds optional authenticated diagnostics under the
new `pi-execution-policy-diagnostics-v1` capability. A supporting client sends
the bounded canonical diagnostic preimage only for FINALIZE and START. The Pi
recomputes and requires its SHA-256 to equal the already-authoritative submitted
policy digest before comparing it with its independently computed local profile.
Mismatch logs contain only fixed server-owned field labels such as
`machine.work_area.x_max` and `air_assist.mapping`; no field values, G-code,
credentials, authorization phrases, endpoints, or client-provided labels are
logged. Malformed, oversized, unbound, or drifted diagnostics fail closed before
controller writes. Older clients remain accepted by a new node, and new clients
omit the optional field when an older node does not advertise the capability.

Focused automated verification passes **120 tests** across the remote client,
Pi server/service, Pi-owned end-to-end execution, durable job store, and remote
node. The complete four-worker Windows suite passes **3,217 tests with 15
expected platform skips**. Repository Ruff passes with `--no-cache` (the normal
cached run encountered pre-existing inaccessible worktree cache directories),
`compileall -q laser_aligner` and `git diff --check` pass, and independent safety
review found no fail-open path or actionable correctness issue. These are
simulated and controller-inert checks.

The updated Pi service restarted successfully at revision `8ce92ee...` with PID
83934 and a new boot ID; camera and authenticated machine services became
available, no job was active, the primary controller remained disconnected, and
the laser remained unarmed. Startup opened the configured secondary controller
only for the required OFF initialization, but the Marlin response contained
startup garbage prefixed to the echoed `M106 S0`, which was rejected as an
unknown command. The node therefore logged
`Secondary Marlin fan startup OFF was not acknowledged`, remained
`secondary_air_assist.ready = false`, and kept Air Assist job START degraded.
This is the next distinct fail-closed blocker. No retry, controller Connect,
Home, motion, arming, laser output, Air Assist ON transition, job FINALIZE, or
physical START was attempted after it appeared.

The current honeycomb/calibration evidence was hashed before investigation and
has not been edited. In particular, `bed_calibration.json`, the automatic
four-edge-fit `honeycomb_support.json` (191 mm span), both detection images, and
both visual-reference files retain their pre-investigation contents. Honeycomb
state is not an execution-policy input and is not the cause of this failure.

## Active Pi-owned secondary-controller Air Assist correction

Work on `feature/air-assist-output` retains the existing binary
`OperationLayer.air_assist` project setting, Cuts / Layers checkbox, persistence,
Undo/Redo, and Material Recipe authority. Imported LightBurn operations still
begin output-disabled. The project schema is unchanged, and all built-in machine
profiles remain Air Assist-disabled.

`MachineSettings.air_assist` is a constrained saved-machine mapping with
`mode`, `fan_index`, `port`, and `baudrate` fields. In addition to the existing
`disabled`, same-primary-controller `grbl_coolant`, and `marlin_fan` modes, this
branch adds `secondary_marlin_fan`. That E3 mapping keeps the primary laser and
motion controller explicitly GRBL while the Pi owns a separate persistent
Creality/Marlin serial connection. Its verified endpoint is
`/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0` at 115200 baud, with
`fan_index = 0`. Windows stores that Pi-local endpoint as opaque configuration
and never opens it. The currently persisted Windows primary endpoint remains
the GRBL `e3bridge://192.168.5.18:8765` endpoint at 115200 baud; the exact
Pi-local primary-controller serial path has not been confirmed and must not be
inferred from the secondary path.

The secondary mapping is deliberately exact: ON is `M106 S255` and intended OFF
is `M106 S0`. It never adds a `P` parameter and never uses `M107`. Physical
bring-up has verified that exact ON command starts FAN2 on the identified
Creality/Marlin controller. Physical confirmation that `M106 S0` stops FAN2 is
still pending, as are full startup, transition, completion, STOP, restart, and
failure lifecycle checks. These software controls are not safety-rated.

Generated secondary-assist jobs encode the strict non-comment line
`E3AIRASSIST <mapping-sha256> ON|OFF` for each transition in the immutable
canonical program bytes. The SHA-256 binds the exact secondary mapping; changing
either that mapping or the schedule changes the finalized program digest. The Pi
validates those instructions and intercepts them before the primary stream, so
the GRBL controller never receives `E3AIRASSIST` or Marlin fan commands.
Malformed, forged, mismatched, or unsupported instructions fail closed. These
programs are E3-specific, are not portable controller G-code, and must not be
submitted through a non-E3 executor.

The layer planner enables assist immediately before the first powered Line,
Fill, or Raster output that requests it, holds it across paths, rapid travels,
passes, and adjacent requesting layers, and disables it before later powered
non-requesting work. Output-disabled, empty, and zero-power layers never enable
it. Preview and Start Here consume the same immutable instructions and preserve
the mapping digest and schedule.

After START ownership is accepted, the Pi owns both execution paths. One
persistent `CrealityControllerOwner` serializes secondary commands and validates
their acknowledgements and timeouts. The Pi-local reader latches passive USB
hangup/read failure, and execution checks that latch between primary program
lines so a lost secondary session fails before further work is streamed.
Secondary command failure fails the job;
the primary GRBL `M5`/STOP path remains authoritative. STOP acts on the primary
first, then runs bounded independent secondary-OFF cleanup so a secondary ACK or
timeout cannot delay primary STOP. Detaching the Windows client causes no fan
transition. Pi restart marks an in-progress job interrupted, never resumes it,
and attempts an acknowledged secondary OFF. This owner must later be shared with
the S1 Z-homing/CR Touch work rather than creating a second concurrent owner;
that separate branch is not merged or modified by this work.

Deterministic Air Assist/Pi/Windows focused verification passed **351 tests with
7 expected Windows POSIX skips**; an additional targeted run for serial-open
START rejection, changed/disabled-config restart recovery, and unresolved-
recovery START blocking passed **3 tests**. The complete Windows repository run
passed **3,118 tests with 15 expected platform skips** in **233.86 seconds**.
Repository Ruff passed with `--no-cache` after the ordinary cached invocation
encountered the worktree's known cache-directory ACL restriction. `compileall -q
laser_aligner` passed with `PYTHONPYCACHEPREFIX` directed to a temporary cache,
and `git diff --check` passed with only Git's existing LF-to-CRLF notices. These
are automated/simulated checks only. The only new physical evidence is the exact
FAN2 ON result above; intended OFF and end-to-end lifecycle behavior remain
pending physical verification.

## Active raster-native geometric primitive recovery

The shared source-neutral native contour fitter now runs a conservative
primitive-recovery pass after its ordinary constrained line/cubic fit and
adjacent merging, but before native-frame and authoritative compound-topology
acceptance. Imported rasters, Camera Trace Contrast and Auto raster strategies,
and the existing physical-contour adapter used by applicable Color/Grid native
paths all enter that one stage. It does not rotate, deskew, blur, widen, or
otherwise preprocess pixels, and it does not change camera normalization,
eligibility, threshold selection, 4x reconstruction, smoothing, or corner
classification.

Candidate lines use deterministic robust orthogonal total least squares and keep
the observed arbitrary angle. Candidate circles use a normalized algebraic
initialization, bounded robust geometric refinement, and endpoint-constrained
open-arc refinement. Acceptance checks every ordered point, maximum and
arc-length-weighted RMS residual, signed bias, support length and sample count,
endpoint movement, independent-half/subsample stability, monotonic order,
angular sweep and sample gap, radius, and source-normal pixel pitch. The
primitive maximum, RMS, endpoint, and canonical-representation budgets are the
stricter of fixed source-pixel fractions and the existing native-fit tolerance;
a large user tolerance therefore cannot erase resolved curvature. Strong
grayscale/alpha threshold crossings can support primitive validation even where
the existing source-edge output remains locked at a hard corner or persistent
straight run.

Each exact source-index partition tries a line, then a conceptual circular arc,
then retains its original fitted line/cubic objects. A primitive may not expand
its own partition or increase the complete contour's segment count. Hard-corner
partitions are preserved; nearby model intersections are accepted only inside
both endpoint allowances, nearly parallel line intersections are rejected, and
smooth joins require at most three degrees of tangent disagreement. Ambiguous
smooth partitions have one bounded, source-backed repartition/refit attempt.
There is no OCR, font/glyph/logo/template inference and no parallel-edge,
constant-width, rectangle, or symmetry regularization.

Accepted conceptual arcs are encoded as mathematically derived canonical cubic
Bézier spans of at most 90 degrees, subdivided further until exact radial-extrema
error satisfies the representation budget, with a 64-span ceiling. Persisted
geometry remains `NativePathGeometry` version 1 with only `PathLineSegment` and
`PathCubicSegment`; preview, G0/G1 planning, guarded G-code, and post-Create
Straighten require no primitive-specific path. If the complete recovered result
fails frame, authoritative native topology, or rasterized hierarchy validation,
the source-identical contour set is refitted once with recovery disabled.
Complexity failures remain fatal. The non-persistent `recover_primitives=False`
argument exists only for development comparisons and fallback; there is no new
Trace-panel setting.

Work is bounded by the existing one-million raw-point limit, 64 hard-corner
partitions, 256 hypotheses per contour, six robust iterations, 4,096 nearby
samples per smooth-boundary search direction, 64 canonical spans per conceptual
arc, and the unchanged authoritative topology comparison limit. Compact result
and Camera diagnostics report baseline/final segment counts, accepted and
rejected primitive counts, recovered lengths, canonical/freeform cubic counts,
worst final raw/evidence maximum and RMS residuals, endpoint movement,
source-pixel scale, and a separate opt-in primitive-recovery timing stage; no
point arrays or primitive
semantics are persisted.

Focused primitive, downstream, curve-fidelity, and Camera routing verification
passes **83 tests** in **37.11 seconds**. The broader shared raster/native,
Camera normalization/eligibility, Object Trace, and diagnostic gate passes
**275 tests** in **154.28 seconds** with four workers. The complete Windows
four-worker suite passes **2,980 tests** with **14 expected platform/privilege
skips** in **253.53 seconds**. Repository Ruff,
`python -m compileall -q laser_aligner`, and `git diff --check` pass.

One uncontended Windows benchmark used an AMD Ryzen 7 8840HS, Python 3.14.4,
NumPy 2.5.2, and OpenCV 4.14.0. Each case prepared one immutable source and exact
4x mask, warmed both modes, then took the median of three alternating
recovery-disabled/enabled runs in one process. Preparation was outside the wall
median; stage values are independently measured inclusive medians and therefore
are not additive. Times below are milliseconds; `D/E` means disabled/enabled.

| Deterministic case | Segments | Primitive D/E | Topology D/E | Native fit D/E | Wall D/E |
| --- | ---: | ---: | ---: | ---: | ---: |
| Small Coleman E | 72 -> 54 | 0.027 / 7.276 | 3.686 / 2.967 | 16.626 / 23.593 | 28.324 / 34.572 |
| Coleman stencil + underline | 244 -> 164 | 0.776 / 103.996 | 33.064 / 31.242 | 1,374.106 / 1,637.644 | 1,654.530 / 1,957.330 |
| Large annular C | 84 -> 47 | 0.088 / 500.434 | 31.004 / 24.537 | 25,794.285 / 27,384.259 | 26,087.073 / 27,658.021 |
| 100-component label | 3,600 -> 1,200 | 3.845 / 1,331.226 | 173.132 / 55.042 | 2,225.415 / 3,671.450 | 4,213.493 / 5,640.069 |
| Medium Camera native fit | 81 -> 49 | 0.239 / 161.652 | 14.373 / 24.346 | 1,900.397 / 2,074.705 | 2,061.962 / 2,310.248 |
| 2x Camera native fit | 392 -> 378 | 0.357 / 206.029 | 25.739 / 51.629 | 3,155.771 / 3,301.498 | 3,410.254 / 3,625.588 |
| Maximum-area 2,048-square import | 134 -> 134 | 0.134 / 31.674 | 44.062 / 40.731 | 31,562.450 / 32,248.796 | 32,011.144 / 32,750.669 |

The representative Camera wall overhead was **12.0%** at medium resolution and
**6.3%** at 2x. The large C and exact maximum-area case were already dominated
by baseline fitting; recovery added **6.0%** and **2.3%** respectively. The
100-component fixture is the largest relative recovery cost at **33.9%** while
removing 2,400 segments; it remains deterministic at 12 hypotheses per contour.
These are synthetic/software measurements, not real-camera or physical-geometry
evidence. A saved physical Coleman capture still needs an operator comparison of
recovery enabled/disabled, broad-C and underline/stem fidelity, small PATENTS
detail, post-Create Straighten composition, final Preview, and guarded output.

## Active post-Create Camera Trace orientation review / Straighten

Straighten is now a normal project edit over selected, finished Camera Trace
artwork. Detect/review has returned to choosing which temporary outlines should
become project objects; it has no Straighten control, Reset control, temporary
rotation state, or rotated candidate overlay. Successful Cut creation selects the
new combined object or the complete separate-object batch and opens the normal
Shape inspector. The optional offer and muted no-offer diagnostic live there.

Eligibility is persistent, non-authoritative SceneObject metadata added only to
successful non-grid native Cut creation. `trace_orientation_eligible`,
`trace_output_mode`, `trace_artwork_id`, member index/count, and creation mode
extend the existing `trace_source` provenance. Stock boundaries, grid-normalized
results, rounded/simplified/exact non-native output, failed native fits, unrelated
project objects, and mixed eligible/ineligible selections do not enter the
estimator. The metadata uses the existing `.e3laser` metadata map and therefore
survives ordinary save/load without a schema change; it grants no planning,
G-code, motion, arming, controller, or laser authority.

`vision/trace_orientation.py` remains Qt-free, image-free, and bounded. Its public
adapter record is `TraceOrientationGeometry(object_id, artwork_id, geometry)`,
where `geometry` is already in current project/world coordinates. The desktop
adapter transforms each selected native path with its current width, height,
mirrors, rotation, and center translation. It neither reads nor reconstructs
pixels, masks, detection dictionaries, contours, or fitting diagnostics.

Each disconnected native subpath is analyzed as a component. Physical-length
lines, demonstrably near-linear cubics, anisotropic component axes, and meaningful
component-center alignment contribute capped evidence to one robust modulo-90
artwork consensus. All components of one combined object belong to one artwork;
all objects from one separate Create batch share the same artwork ID. Curved and
tiny local fragments can contribute little evidence but cannot independently veto
their own artwork. The conservative disagreement veto instead compares reliable
orientations from distinct selected artwork IDs, so two separately created labels
at incompatible angles remain conflicting. Total analysis remains capped at
20,000 native segments and 8,192 subpaths.

The existing confidence and angle gates remain conservative: under 0.4 degrees is
trivial, ordinary offers stop at 10 degrees, 10–15 degrees needs exceptional
confidence, and larger corrections are suppressed. Successful UI copy states the
detected direction and the opposite correction direction. Eligible no-offer
selections show a muted already-straight, insufficient-evidence,
conflicting-evidence, or out-of-range explanation; ineligible selections show no
Camera Trace-specific control.

The estimator pivot is the exact center of the union of selected world-native
bounds. Clicking **Straighten** recomputes from current geometry, rotates every
selected object center about that one pivot, adds the same correction to every
object rotation, and commits one standalone `UpdateTransformsCommand`. Create and
Straighten are separate history entries. Undo restores the command's exact saved
pre-Straighten transforms; Redo reapplies its exact saved corrected transforms.
Local native geometry is never rewritten, so line/cubic types, subpaths, holes,
islands, fill rule, topology, and relative spacing remain intact. Selection
changes, ordinary transforms, and Undo/Redo recompute only from current project
geometry, without camera, normalization, threshold, raster reconstruction, or
native-fitting work.

Focused post-Create estimator, panel, selection, creation, history, provenance,
and persistence verification passes **87 tests** in **1.67 seconds**. Broader
Camera Trace eligibility, raster threshold, native fitting/topology, grid,
source-control, and template regressions pass **169 tests** in **107.17 seconds**.
The complete Windows four-worker suite passes **2,917 tests** with **14 expected
platform/privilege skips** in **143.46 seconds**. Repository Ruff,
`python -m compileall -q laser_aligner`, and `git diff --check` pass. On this
Windows host, 200 warm end-to-end selection estimates (SceneObject adaptation
plus estimator) measured **0.9437 ms median / 0.9603 ms p95 / 1.0538 ms max**
for one combined four-component label, **10.9183 / 11.1325 / 12.0645 ms** for
one combined 92-subpath stencil, and **1.0141 / 1.0314 / 1.0814 ms** for one
four-object batch. These are synthetic software timings, not real-camera
performance evidence.

The implementation was prompted by newer operator-reported Coleman stencil
evidence: manual threshold 128 produced a dramatically cleaner production Mask
but later failed bounded native-topology validation, manual threshold about 150
produced usable geometry, and Auto selected 170 from the image and produced a good
trace. Controller, firmware, machine configuration, capture identity, and measured
placement results were not recorded in that report, so it does not satisfy the
repository's formal physical-acceptance record. Post-Create Straighten still needs
a recorded physical test of combined and separate creation selection, offer and
correction direction, shared-pivot placement, Undo/Redo, saved/reloaded provenance,
final Preview, and guarded generated output.

## Active Pi-owned desktop Disconnect generation correction

A physical Pi-owned desktop run reproduced **Disconnect** failing with
`Operation was cancelled by software STOP` before the controller-disconnect RPC
was sent. The exact cause was the interaction between request-time cancellation
and Disconnect's intentional revocation. `DesktopController._run()` captured and
thread-bound generation N; `RemoteMachineService.disconnect()` advanced the
STOP epoch to N+1 so queued and in-flight pre-START work would become stale;
`_machine_status_action()` then read the still-bound N and rejected Disconnect's
own cleanup before network transport.

Idle Disconnect now captures N+1 while performing that same revocation and
binds only its `machine.disconnect` cleanup RPC to N+1. Ordinary connect,
Home/park, jog, manual-command, upload/FINALIZE/START, and all other operation
paths retain their request-time generation. A later STOP or detach advances the
epoch again and still rejects the cleanup at its existing pre/post-RPC checks;
STOP remains an independent priority RPC. If upload/START preparation already
owns the operation lock, Disconnect retains the prior local detach behavior: it
cancels the stale preparation without sending controller Disconnect or STOP. An
accepted or ownership-uncertain Pi job likewise remains non-destructively
detached and continues under Pi authority.

The related lifecycle paths do not have the same inversion. Remote
`replace_connection()` is one Pi-side atomic ordinary action and does not advance
the Windows facade's generation. Direct `detach()` advances the generation but
has no cleanup RPC to self-cancel. Desktop shutdown calls detach first to revoke
workers; accepted/uncertain Pi execution stays detach-only, while later idle
`AppContext.stop()` uses the distinct short-budget remote shutdown path outside
the stale desktop worker scope.

A deterministic offscreen desktop regression exercises the exact
`DesktopController._run() → operation_scope() →
RemoteMachineService.disconnect()` path and observes one and only one
`machine.disconnect` RPC with no UI error. Remote-service regressions separately
cover blocked CHUNK and FINALIZE cancellation, accepted-job detach without an
RPC, idle disconnect, idle runtime shutdown, accepted-job runtime shutdown, and
a STOP overtaking an in-flight Disconnect RPC. The focused desktop, remote
facade, and Pi server batch passes **48 tests**. The complete combined-branch
Windows four-worker suite passes **2,851 tests** with **14 expected
platform/privilege skips**; repository Ruff, `python -m compileall -q
laser_aligner`, and `git diff --check` pass. The corrected Disconnect path has
automated Windows verification only; it has not yet been re-run against the
physical Pi/controller.

## Active Pi-owned parked-camera hold ordering correction

Physical reproduction on `main` commit `66a704653ddabee7f352b4c5eaa779d2bc3688ce`
showed Camera Trace enter Working while the controller was HOME REQUIRED, remain
motionless for about two minutes, then Home/park and complete normally. The
delay matched the Pi server's 120-second stepper-hold lease.

The exact cause was a cross-session ordinary-operation deadlock. Windows
`RemoteMachineService.temporary_stepper_hold()` establishes one long-lived
authenticated `E3MACHINE/2` session. The Pi server enters
`PiJobService.temporary_stepper_hold()`, which deliberately owns
`_ordinary_lock` across the complete yielded lease. `AppContext` then sent Home /
park through a second ordinary RPC while the first session still owned that
lock. Windows could not release the hold until Home / park returned, and Home /
park could not return until the hold released. Only the server's finite lease
timeout broke the cycle.

All six `AppContext` hold call sites have been audited and now keep the hold
capture-only. Object Trace and fresh base-bed mapping always complete Home /
park before hold acquisition. Dense calibration, accuracy validation, and fine
registration do so when `home_first=True`; their intentional no-home recaptures
acquire the hold directly and send no machine motion inside it. Coordinate audit
completes Home / park and its before-capture position sample before the hold,
captures the raw burst while held, releases, then takes its after-capture sample.
No ordinary machine RPC is nested in a remote hold. Raw-frame capture remains
inside the hold; sharpness scoring, lens correction, rectification, detection,
and analysis remain outside it. A failed Home / park never acquires a hold, and
a capture exception still exits the same-channel release path.

The Pi lock, same-channel hold/release authentication, finite 120-second lease,
idle requirement, Pi-owned job exclusion, controller authority checks, and STOP
bypass were not weakened or retimed. A direct server regression proves an
ordinary Home / park request remains serialized until another session releases
its hold. A second regression proves STOP still returns immediately during a
hold; the canceled held session reports failure rather than claiming a normal
release.

One deterministic full-stack loopback capture runs
`AppContext → RemoteMachineService → E3MACHINE/2 → PiMachineServer →
PiJobService → MachineService`. It proves prepare completion precedes hold
acquisition, the GRBL hold is active during the camera burst, and normal release
restores the finite idle delay. On the local simulated controller/camera sample,
prepare-photo took **0.0171 s**, hold acquisition **0.0138 s**, the stubbed raw
burst **0.000055 s**, the complete precision-capture scope **0.0316 s**, and the
already-connected end-to-end Trace capture **0.0321 s**. These are deterministic
software-loopback timings, not a physical camera-performance measurement. The
previous approximately 120-second value is the physical failure observation;
the corrected sequence has not yet been re-run on the Pi/controller/camera.

Focused Windows verification passes **149 tests** across RemoteMachineService,
PiMachineServer/PiJobService locking and STOP behavior, local MachineService
hold/Home behavior, Camera Trace precision capture, base mapping, coordinate
audit, fine registration, dense/accuracy workflows, and desktop Trace capture.
The complete Windows four-worker suite passes **2,848 tests** with **14 expected
platform/privilege skips**. Repository Ruff, `python -m compileall -q
laser_aligner`, and `git diff --check` pass. No timeout constant changed, and no
new physical motion, laser, camera, or accuracy verification is claimed.

The ordinary corrected live overlay was separately reported unavailable during
the physical reproduction, so the operator used Raw Live Monitor. That overlay
issue was not investigated or changed here and remains a separate follow-up.

## Active development-update publication continuity

The `Publish E3 development update` workflow no longer deletes and recreates
the live `e3-development` prerelease. Windows and Linux builds still complete
independently before publication, but the publish job now gives their outputs
immutable revision-specific names (`E3-Setup-<12-sha>.exe` and
`E3-<12-sha>-x86_64.AppImage`). A Qt-free Python publisher validates the
generated schema-1 manifest against the exact local files, uploads both
packages, and verifies GitHub's uploaded state, byte size, and server-reported
SHA-256 before it uploads the staged manifest.

The stable updater endpoint remains
`releases/download/e3-development/update-manifest.json`. GitHub provides no
in-place content replacement for a release asset: CLI clobber deletes before it
uploads, while the asset API can only rename metadata. The final switch is
therefore a recoverable near-atomic pair of renames from the old stable manifest
to an ID-specific backup and from the already-uploaded staged manifest to the
stable name. Cancellation signals are deferred across that critical pair, and
an always-run workflow recovery command restores the old verified manifest if
normal concurrency cancellation interrupts the publisher. Failures before the
switch retain the old manifest and packages; failures after it retain the
complete new packages and manifest. The old manifest backup alone is cleaned
up afterward on a best-effort basis. Prior binary assets are not removed, so a
client holding the prior manifest can still download and verify its referenced
package. Release tag, title, body, target, and prerelease metadata change only
after the new manifest is authoritative.

Desktop manifest retrieval now retries only transient HTTP 404, 408, 429, and
5xx responses with bounded 0.5, 1, and 2 second delays. Malformed URL and JSON
failures remain immediate; package downloads are not retried by this path, and
existing channel, revision, exact-size, SHA-256, and installer verification is
unchanged. Focused updater/workflow/publisher verification currently passes
**24 tests**; broader deployment, versioning, desktop handoff, and Windows
launcher regression verification passes **49 tests** with four xdist workers.
The complete Windows suite passes **2,656 tests** with **14 expected platform
skips** and four workers. Repository Ruff, Python compilation of `laser_aligner`
and `packaging`, workflow YAML parsing, and `git diff --check` pass. These are
local mocked-network and deterministic publisher tests; the actual Windows
installer and Linux AppImage for this change have not yet been built or
published, and no controller, motion, arming, laser-output, or physical test
was performed or is claimed.

## Active Camera Trace material eligibility, Auto orchestration, and raster parity

Ordinary non-grid Camera Trace now removes machine/background responsibility
from the raster vectorizer. The production order is corrected frame, hard
physical Trace ROI, trusted empty-honeycomb comparison, material eligibility,
eligibility-scoped illumination normalization, dark/light threshold, eligibility
gate, and the unchanged shared raster-vector geometry pipeline. The full frame
and its established pixel-center transform are retained.

In machine coordinates the hard ROI is the existing guarded output polygon, or
its existing guarded rectangle when no polygon is configured. In current
honeycomb-local coordinates it is the intersection of that guarded geometry
with the recorded support rectangle in the already established local frame.
This narrows vision evidence only. It does not expand support, output authority,
planning, G-code, motion, arming, STOP, or machine execution. A foreground root
clipped by that hard ROI is rejected before review and Auto scoring.

The controller supplies an empty-bed image only through the existing accepted
background path, after schema/kind, encoded-image SHA-256, complete bed-map
digest, support-frame digest, coordinate-frame, rectification, and final-image
dimensions validate. The comparison model is bounded to 800 pixels and at most
2 pixels/mm. It uses correlated locally detrended luminance, normalized patch
error, and compatible texture to propose structural bed evidence, but structure
alone no longer excludes pixels. Strong reference-like seeds with uncompensated
Lab luminance/chroma deltas no larger than 32/24 levels drive deterministic
Tukey-weighted compensation. Luminance uses a bounded 0.72–1.28 reference gain,
±48 offset, and ±32 whole-frame X/Y gradients; chroma uses ±24 offsets and ±16
gradients. The compensated point residual must meet 34/22/38
luminance/chroma/combined limits and its 1.5 mm patch mean must meet 26/18/30,
except that a strict 12/8/14 point match preserves true bed immediately beside a
changed boundary.

The 3 mm-radius exposed-evidence closing remains for real honeycomb continuity,
but now runs at bounded model resolution only from strong structural-plus-
appearance seeds. It may add only appearance-consistent pixels with loose
structural support. A single false seed cannot expand and closing cannot bridge
through clearly changed material. Broad brightness, white-balance-related drift,
gradient, mild blur, noise, and local reflections are covered by deterministic
fixtures. Changed or uncertain pixels remain eligible; the stock is eligibility,
not foreground.
Honeycomb-local Auto fails closed when no valid reference is available. Manual
Contrast and explicit Color may use clearly diagnosed hard-ROI-only fallback
when no reference exists; a supplied mismatched reference is rejected.

Camera Trace now has one detection-and-review workflow. The former seeded
**Cutout / silhouette** mode, prepared-frame state, Add-click lifecycle, quick
blue outline, and second asynchronous verification state have been removed.
Auto, By color, and By contrast produce the candidates. With **Use grid** off,
Auto is now an orchestrator over production tracing paths rather than another
independent detector: it prepares the corrected frame once, estimates one
  material eligibility, estimates one camera-raster background, derives
  symmetric dark- and light-feature rasters,
selects one bounded source-resolution Auto threshold for each immutable result,
conditionally tries
  Color only when eligible material contains bounded non-background chroma, and
  chooses a credible verified result or fails closed. With **Use grid** on, Auto
  deliberately retains the specialized
repeated-object detector, lattice fitting, cell normalization, missing-cell
inference, and damaged/open-cell review. A stored legacy
`cutout` preference is migrated once to `contrast`; the old value cannot leave
the color picker disabled. Desktop tests redirect organization/application
`QSettings` into a unique per-worker INI file, so parallel runs neither inherit
nor mutate the operator's real preferences.

Temporary candidates are real selectable canvas items over the frozen corrected
camera frame. Click selects one, Ctrl-click toggles, empty-space click clears,
and a rubber band selects multiple direct candidates. Inferred grid positions
remain explicit and are not silently promoted by a rubber band. The inspector
checkboxes and canvas use the same detection-ID set. Smaller overlapping
candidates receive deterministic hit priority; selection survives zoom and pan,
and a new detection, Clear, camera/calibration invalidation, or creation removes
the old temporary items. While review is active, normal project objects cannot
be selected or moved; their prior selection and flags are restored afterward.
Preview candidates never enter the project document, planning cache, G-code, or
execution paths.

Starting a new detection immediately removes the preceding temporary candidates
while leaving project objects untouched. Before native fitting, the Trace panel
can switch the frozen camera display among the corrected **Camera**, exact
source-resolution production **Exposed bed** mask, exact source-resolution
**Eligible** mask, normalized grayscale, and exact production **Mask**. Exposed
bed is the same immutable array inverted to form material eligibility, not a UI
approximation. Raster Mask is the immutable 4× binary workspace passed to the
selected contour extractor (`RETR_TREE` for Full detail or external-only
extraction for Outer silhouette), not a reconstructed UI approximation. Its
display uses its actual 4× pixel scale so all four images occupy the same
physical area. Request
IDs and the camera-review signature reject stale preview, completion, and failure
callbacks. If fitting fails after mask preparation, the camera hold and diagnostic
views remain available until Clear or the next detection; a failure before a
deliverable preview returns to live camera state.

Physical build `26c5943` confirmed that Camera, Eligible, and Normalized looked
plausible but exposed a Mask-only presentation failure: the selector and status
changed while the workspace retained the corrected Camera pixmap. Subsequent
runtime tracing and the new automated pixel regression confirmed that the exact
4× QImage was present and byte-distinct in the Mask slot. The failure occurred
when the workspace independently re-rounded the fractional corrected-image area
at 4× pixels/mm: `4 × round(area × base_ppm)` is not always equal to
`round(area × 4 × base_ppm)`, so the exact production dimensions were rejected
before `setPixmap()`. The desktop now declares the integer source-resolution
multiplier explicitly, validates the 4× image against four times the already-
rounded source raster, and retains the actual 4× transform. No preview image is
resized and the immutable production mask is not copied back into or changed by
the display path. Temporary application-log diagnostics record dimensions,
format, byte count, and a padding-neutral pixel SHA-256 for every stored Camera,
Exposed bed, Eligible, Normalized, and Mask image.

Focused offscreen verification passes **93 tests** across the asynchronous
desktop camera/Trace path, real workspace rendering, and Trace panel behavior.
Repository Ruff, `python -m compileall -q laser_aligner`, and
`git diff --check` pass. This is automated display verification only. The fix
has not yet been exercised in a physical build after `26c5943`; no new live
camera, controller, motion, arming, laser-output, cutting, or physical-accuracy
verification is claimed.

Non-grid **By contrast** no longer enters the multi-hypothesis object detector.
The corrected BGR frame is first hard-gated and reference-suppressed, then
converted to eligibility-normalized raster artwork and sent through the same
production pipeline as an imported raster:
source-neutral immutable RGBA preparation, Otsu or manual thresholding with
explicit polarity, physical connected-component and pinhole cleanup, 4× mask
reconstruction, bounded detail-selected contour extraction, physical contour
mapping, source-edge refinement, and the authoritative native line/cubic fitter
plus all topology checks. In Full detail each root foreground contour and all
descendants form one review candidate; Outer silhouette passes only each
external root. Minimum area remains the raster cleanup scale. A conservative
pre-fit root filter can omit a complete indivisible tree only when its threshold
bounds and the fitter's full displacement allowance prove that maximum area or
minimum width/height cannot pass; near-limit, smoothed, and ambiguous trees stay
for the unchanged authoritative post-fit review. The one final raster-local-to-
camera affine accounts for Y direction, pixel centers, work-area origin, exact
pixels/mm, and a possible fractional edge strip; there is no camera-side contour
extraction, second fit, or post-map refit.

The camera-specific normalization model is Qt-free and does not threshold or
repair output geometry. It converts the corrected image to uint8 grayscale,
fills ineligible pixels only in the temporary background-model input, derives
its robust response scale from eligible material, forces excluded response white,
and builds a temporary model bounded to 1 pixel/mm and 512 pixels on its long
axis. It normally computes 35 mm elliptical opening and closing envelopes and
smooths each with a 4 mm Gaussian. The closing supplies the dark-feature
background and the opening supplies the light-feature background; their
midpoint is retained only as signed diagnostic context. A narrowly gated clean/flat-
field path instead uses one constant robust border level only when four-level
histogram bins, a 2 mm border band, whole-model background dominance, and
far-versus-intermediate separation all pass their conservative gates. Continuous,
quantized, and machine-border shadow cases therefore fall back to the rank
envelope.

The one-sided float32 distances are closing-minus-image for dark features and
image-minus-opening for light features. Only the larger distance wins at a
pixel; ties stay blank. This retains exclusive polarity while avoiding the old
midpoint amplitude, which could treat half of a glyph as background and let a
darker surface mark cancel adjacent sound pixels. After a three-level noise
floor, one nearest-rank 99.5th-percentile magnitude clamped to 32–64 levels
supplies the shared response scale `R`. Dark and light uint8 artwork use the reciprocal
transfer `round(255R / (R + X))`: blank/opposite-polarity is 255, response `R`
is 128, and stronger response approaches black without hard clipping. Camera
Auto now generates at most 12 thresholds from that exact normalized raster:
stabilized Otsu, Triangle, Otsu-to-class-median interpolation, and 1/3/8/16/30%
foreground-occupancy quantiles. It scores source-resolution coherence, nearby
mask/component/hole stability, specks, occupancy, eligibility-border dominance,
retained coherent area, and narrow retained foreground before any 4× work or
native fitting. A credible non-Otsu winner must clear a baseline departure margin
that grows when it adds more than two components or worsens border occupancy.
No captured or physically successful threshold byte is a candidate constant.

The Otsu baseline can advance its lowest equally optimal plateau member by at
most two unused levels inside an empty histogram gap when the low class lacks
interpolation headroom. Normal polarity measures the selected foreground span;
inverted light polarity measures above the low background endpoint. Camera Auto
uses only eligible pixels; ineligible pixels are forced background before cleanup
and again by a nearest-neighbor gate at 4×. With no eligibility, ordinary
imported-raster Otsu and manual-threshold semantics remain unchanged.

The shared 4× mask path now constrains bicubic reconstruction to its proper
role: localizing a boundary inside the one-source-pixel transition band. Every
cleaned source pixel whose complete 3×3 neighborhood is foreground or
background is nearest-neighbor locked to that same classification at 4×. This
prevents cubic ringing from inventing a positive-area hole or island where the
source mask contains no boundary, without filling real holes, joining gaps,
changing component cleanup, or replacing subpixel edge localization.

The physical stencil failure `A retained raster contour has fewer than three
distinct points` was traced to the 4× reconstruction itself. Bicubic grayscale
interpolation can overshoot near a retained edge inside the deliberately dilated
one-source-pixel component gate; one nominal-background sample can therefore
cross the threshold and become a one- or two-point, zero-area `RETR_TREE`
contour even after base-resolution component cleanup. Shared source-neutral
contour pruning now removes only nodes with fewer than three distinct trace
points or exactly zero trace-pixel polygon area. Positive-area contours remain
eligible regardless of size. The complete `next`, `previous`, `first_child`,
and `parent` hierarchy is rebuilt in original sibling order. A degenerate node
whose subtree contains legitimate geometry causes its entire root tree to be
rejected rather than reparenting descendants and inventing a new even-odd
topology. Quick Preview, exact imported-raster vectorization, and camera raster
strategies all receive the same repair and diagnostics.

The normalization regression is demonstrated by four long dark glyphs on a
continuous camera gradient with within-glyph tone variation and darker surface
marks. The former midpoint path retained **20.6%** of the known solid cores at
manual threshold 128 and fragmented them into **97** foreground components; the
one-sided winner-gated responses retain **100%**, reject all tested clean
background, and produce the expected **four** components. A separate exact-mask
fixture starts with a solid source rectangle and a selected 2×2 intensity
plateau at level 127 under threshold 128. Unconstrained cubic reconstruction
reached level 145, created 24 background pixels, and produced a positive-area
child hole; the homogeneous-interior guard retains one root and no hole. A
camera-glyph integration fixture preserves four real roots and its one intended
hole, and the exact 1,170 × 444 Coleman source at threshold 122 remains 50
components, 50 roots, and zero descendants in a local read-only diagnostic.

The pixel-vectorization source, exact prepared-mask value, and result are source-
neutral and defensively immutable on bytes backing stores. Imported assets wrap
that contract with their real `RasterAssetIdentity` and exact encoded-byte
verification; live normalized camera pixels use a versioned content-derived key
and do not invent file metadata, paths, or SHA provenance. Non-grid contrast
exposes bounded-Auto/manual threshold and local light/dark response controls, visibly
disables the hue controls, and uses the native raster-vector output without a
border offset. With
**Use grid** enabled, By contrast deliberately retains the specialized
multi-mask object/grid detector, classification, normalization, and gap
  inference. By color remains the explicit operator-controlled color path and
  obeys the hard ROI.

For non-grid Auto, both raster polarities reuse one immutable normalization and
background estimate while each owns its exact immutable
`PixelVectorizationSource`, bounded Auto threshold selection, prepared mask, physical
minimum-feature cleanup, 4× reconstruction, hierarchy, source-edge refinement,
  and native validator. Color is attempted only when eligibility-scoped weighted
  HSV/Lab evidence covers enough material, at least 60% of the chroma weight lies
  in one ±14-hue window, and that window is at least 1.5× the strongest separated
  competitor. Its mask must cover 0.2–35% of eligible material and at most 25%
  of the eligibility boundary. A credible Color result must beat the best
  credible raster result by eight points. Auto ignores saved manual color
  samples and uses hue tolerance 14 and minimum saturation 45 for this bounded
  attempt; explicit **By color** keeps the operator's controls but obeys the hard
  ROI.

Completed strategies are scored deterministically without a positive candidate-
count term. The score is `40V + 20F + 15B + 10A + 10S + 5W - P`: `V` is the
valid independent-root ratio, `F` is useful foreground occupancy, `B` is
non-foreground border quality, `A` is useful retained physical area, `S` is the
fraction of retained roots at least four times the minimum feature area, `W`
is the in-frame candidate fraction, and `P` is a 35-point frame/background
penalty when both foreground and border occupancy reach 75%. A strategy with no
authoritative native candidate or a score below 70 is rejected, and one with at
least 95% foreground plus at least 75% border occupancy is rejected as
background-dominated. Only post-ROI, post-reference, within-output, verified
candidates contribute positive evidence. Stable
ties prefer dark raster, then light raster, then Color. The result message names
the selected strategy, exact Auto threshold or hue/tolerance, valid count, and omitted
invalid/filtered count; bounded per-attempt metrics and failure reasons remain
internal. Serialized Auto results retain `detection_mode=auto` while reporting
the effective selected native/Auto-or-Color options; the original request and
effective options are both preserved in diagnostics. Review-filtered roots count
as unavailable even when they did not reach topology fitting, and an all-pruned
raster attempt retains exact root count, bounds, stage, and failure reason.

A failed strategy does not abort Auto. Native fitting and every existing
frame/extrema, continuous-error, self/adjacent-arc, compound-clearance, even-odd,
and rasterized-hierarchy validator remain authoritative. Auto isolates a
failure only at an independent root-tree boundary: one root plus all holes and
islands is fitted and accepted or rejected as one indivisible unit. It never
separates a compound tree to escape validation. Other verified roots remain
available for review, while the rejected tree and its bounded reason stay in
diagnostics and are not selectable or creatable. The raster path first runs each
ordinary validator against the complete forest; only a non-complexity failure
triggers per-root diagnosis. Survivors are rebased and the unchanged global
validators run again. If all roots pass alone but fail together, the survivor
forest still fails, and every complexity-limit failure remains fatal to the
strategy.

In the Trace panel, non-grid Auto owns polarity, bounded threshold selection, and optional
color selection. Hue/sample and threshold/polarity controls are therefore
inactive, output is fixed to authoritative **Native lines / Béziers**, and border
offset is fixed at zero. Explicit **By color** alone enables hue/sample controls;
explicit non-grid **By contrast** alone enables manual threshold and polarity.
Auto with grid enabled preserves the specialized grid output and normalization
controls. Minimum feature area, review filters, native fit tolerance, output/work
authority, grid toggle, and selection controls remain available where applicable.
The read-only **Chosen threshold** value is `—` before detection, displays the
exact production byte after a successful non-grid Auto raster result, displays
`N/A` when Auto selects Color, updates on a new result, and returns to `—` on
Clear, failure, or settings staleness. Manual mode retains its editable byte and
does not receive the Auto value. The minimum area, width, and height defaults
remain 30 mm², 4 mm, and 3 mm.

Cut geometry offers two explicit commits. **Create separate vectors** creates
one editable object per selected candidate. **Create one combined vector**
creates one logical even-odd compound path containing all selected subpaths;
overlaps are deliberately preserved and are not unioned. Either operation is a
single undoable project edit. Stock-boundary creation remains a separate
single-outline, non-output path.

Focused Qt-free and offscreen regressions cover a reflective periodic honeycomb,
trusted empty-bed reference, machine surround, 20/50/84% stock coverage,
exposure/gradient/white-balance-related drift, blur/noise/highlight, empty bed,
blank stock, dark/light stencil artwork, a dark nearly vertical stencil with
reference-correlated normalized texture, appearance mismatch, closing
amplification and true-bed continuity, holes, narrow gaps, underline, hard-ROI
invariance, false warm Auto Color, real bounded Color, Auto fail-closed,
four-edge coordinate mapping, and exact Camera/Exposed-bed/Eligible/Normalized/4× Mask
switching, including complete stored-QImage and actual workspace-pixmap pixel
identity across a fractional-edge display case. Existing coverage also includes
the low-frequency background
model and its adversarial flat-field gates; dark/light reciprocal responses;
gradient, shadow, machine-border, gap, hole, dense-label, clean-raster, noisy-
solid, and true two-level cases; symmetric Otsu plateau stabilization; immutable
normalization/source/mask results; literal non-grid components; exact 4× mask
publication and reuse; degenerate-leaf pruning and hierarchy repair; independent
compound-tree isolation; conservative pre-fit review filtering; Auto's one-
background dark/light reuse and conditional Color route; native geometry and
imported-normalized-camera parity; request/signature staleness; retained failure
diagnostics; immediate old-candidate retirement; Trace panel controls; capture
and rectification timing; and the standalone raster diagnostic command.

Before integration, the broader eligibility, normalization, object-Trace,
native-raster, and desktop preview selection passed **231 tests** in **78.47
seconds**. After merging the Pi-owned execution mainline, one combined focused
Trace/Pi batch passed **383 tests** in **73.38 seconds**, and the complete local
Windows four-worker suite passed **2,843 tests** with **14 expected platform
skips** in **146.84 seconds**. Repository Ruff,
`python -m compileall -q laser_aligner`, and `git diff --check` pass. These are
deterministic Qt-free, source-level, loopback/simulator, and offscreen-widget
checks; no interactive GUI, live camera, or hardware validation is implied.

For the rank-envelope and homogeneous-interior corrections above, a fresh
Windows focused batch covering camera normalization and trace, eligibility,
shared raster vectorization, object Trace, native fit acceptance and curve
fidelity, desktop trace sources/layout/async integration, and the standalone
camera-raster diagnostic passed **328 tests** in **304.91 seconds**. Repository
Ruff, `python -m compileall -q laser_aligner`, and `git diff --check` also pass.

For the bounded Camera Auto threshold selection and exact chosen-threshold UI,
the final focused Windows four-worker batch passed **313 tests** in **235.31
seconds** across shared raster vectorization, camera normalization and Trace,
eligibility, Auto orchestration, Trace panel state, async desktop integration,
preview sources, and the standalone diagnostic. The complete Windows four-worker
suite passed **2,862 tests** with **14 expected platform/privilege skips** in
**238.22 seconds**. Repository Ruff, `python -m compileall -q laser_aligner`,
and `git diff --check` pass. This is deterministic Qt-free and offscreen-widget
verification only; no interactive GUI, live camera, controller, motion, arming,
laser-output, cutting, or physical-accuracy verification is claimed.

On the 640 × 480 correlated-texture stencil fixture at 2 pixels/mm, ten
post-warmup eligibility samples had median stage times of **25.081 ms** for
structural reference matching, **56.212 ms** for photometric compensation,
**8.871 ms** for the appearance veto, **2.258 ms** for guarded closing, and
**108.828 ms** total eligibility. Observed total eligibility ranged from
101.808–113.935 ms. The comparison remains bounded to 800 pixels, 2 pixels/mm,
and at most 50,000 deterministic photometric-fit samples. These timings are
development-machine samples, not performance guarantees or physical-camera
measurements.

At the time this correction was recorded, the reported Coleman stencil scene had
not been recaptured or replayed through a physical camera. The later 2026-08-30
operator report is recorded in the Straighten section above but lacks the details
required for formal physical acceptance. No controller, motion, arming,
laser-output, cutting, or physical-accuracy test is retroactively claimed for
this earlier correction.

## Active development-release trigger filtering

The `Publish E3 development update` workflow still runs automatically for
application/source, packaging, release-workflow, and runtime-dependency changes
on `main`, and it retains manual `workflow_dispatch`. Its push trigger now uses
an explicit `paths-ignore` list for normal CI workflows, repository templates
and instructions, non-installed documentation, tests, and the development-only
requirements file. GitHub applies `paths-ignore` only when every changed path
matches the list, so a mixed commit containing any unignored product-affecting
path still builds and publishes the Windows installer, Linux AppImage, update
manifest, and `e3-development` prerelease. Job definitions, main-branch guards,
and `cancel-in-progress: true` are unchanged.

Focused update/workflow verification passes **8 tests**. PyYAML independently
parses the workflow and verifies its trigger, concurrency, three-job structure,
and Windows/Linux-to-publish dependency. Ruff on the affected test,
`python -m compileall -q laser_aligner`, and `git diff --check` pass. No E3
application, controller, motion, arming, laser-output, packaging, manifest, or
publishing implementation changed; no package build or physical test was
required or performed.

## Active raster-vectorization source-edge localization

The adaptive local per-span tolerance experiment from commit `4039047` is
fully reverted. The 0.10 mm user-facing native fitting tolerance again supplies
the existing fixed 80% internal budget (0.08 mm at the default) to every span.
The earlier straight-edge recovery, curved-span distribution centering and
bounded Newton refinement, two-stage responsive preview, native line/cubic
persistence, continuous maximum-error proof, frame/extrema validation,
self/adjacent-arc and compound topology checks, hierarchy validation, project
path, and planning behavior remain in place.

Before an authoritative exact fit, each independent threshold contour now
retains its original topology while eligible samples are localized against the
original source raster. The local normal uses 1.25 source pixels of contour
support. The exact composited grayscale and alpha fields are bilinearly sampled
from -1.25 through +1.25 source pixels at 0.125-pixel intervals, using the same
manual/Otsu/alpha threshold, inversion, and alpha-gate semantics as
segmentation. An accepted sample must contain exactly one strong outward
foreground-to-background crossing, sufficient endpoint margin, contrast and
slope, bounded reverse variation, and a displacement no larger than 0.6 source
pixel. Profile work is bounded in 8,192-point chunks. Flat, noisy,
multiple-crossing, out-of-frame, and otherwise unsupported samples stay at the
threshold position.

The original threshold contour remains the classification authority. Detected
hard corners and their adjacent support points, persistent straight runs, and
straight spans promoted between hard anchors are never shifted. Nested parent
and hole contours are conservatively not refined. The source-edge maximum shift
is added to the ordinary smoothing/fitting/preview deviation envelope, and all
existing frame, continuous-error, topology, clearance, and 4× raster-hierarchy
validation still runs. Quick Preview remains the unchanged display-only
threshold outline; source-edge localization runs only in the exact worker.

On the exact 1,170 × 444 Coleman source (SHA-256 beginning `e72143e3`) at
80.0 × 30.358974 mm, manual threshold 122 and no smoothing, the critical P-bowl
span retains 147 samples and one cubic. The restored cubic-to-threshold
maximum/RMS/signed-mean errors are 0.066449/0.033027/-0.005640 mm. The
threshold-to-source displacement is 0.011856 mm maximum, 0.006583 mm RMS, and
0.005994 mm mean outward. This is only 20% of fit RMS, so source localization is
not the dominant total-error term, but it is comparable to the systematic
centering bias. Against the refined source edge, the restored cubic measured
0.068650/0.034286/-0.011571 mm; the refitted cubic measures
0.064342/0.035779/-0.004843 mm. Maximum error falls 6.3% and systematic inward
bias falls 58%. The complete P bowl changes from 15 to 14 segments rather than
adding pixel-scale pieces.

Exact Coleman A/S source-relative maximum and RMS errors improve from
0.073212/0.022554 to 0.059191/0.020840 mm and from 0.072049/0.025475 to
0.065321/0.022589 mm. Their segment totals fall 23→19 and 32→31. The E retains
72 segments and identical 0.013377/0.002304 mm source-relative maximum/RMS
because its straight and corner evidence is protected. The diagnostic image and
full method are recorded in
[`docs/diagnostics/COLEMAN_SUBPIXEL_SOURCE_EDGE.md`](docs/diagnostics/COLEMAN_SUBPIXEL_SOURCE_EDGE.md).
The complete real source changes from 930 to 913 native segments.
The complete real large Coleman `o` crop changes from 48 to 47 segments while
aggregate source-relative maximum/RMS error improves from
0.081235/0.025569 mm to 0.079563/0.022141 mm. A separate supersampled analytic
D-bowl known-geometry control also improves maximum and RMS error without
adding segments.

One prepared-source timing session measured useful Quick Preview at a 0.0358 s
warm median. It remains outside source-edge localization. Two warm exact-fit
runs measured 4.677 s at the restored threshold-only baseline and 5.697 s with
source-edge localization. Focused real Coleman P/A/E/S, analytic known geometry,
one-pixel phase translation, rotated/scaled resolution convergence, ambiguous
profile rejection, chunk equivalence, straight/corner preservation, fitter,
dialog, and desktop vectorization verification passes **118 tests** with four
xdist workers. Broader native-path, project/history, toolpath, planning/golden,
digest/cache, preflight, and desktop native-path verification passes **408
tests**, also with four workers. Repository Ruff,
`python -m compileall -q laser_aligner`, and `git diff --check` pass. This is
automated Qt-free/offscreen authoring analysis only;
no interactive GUI, camera, controller, motion, arming, laser-output, or
physical-accuracy test was performed or is claimed.

## Active raster-vectorization curved-span centering

The exact cubic fitter now checks the spatial distribution of a material
candidate's error before accepting an otherwise tolerance-compliant
chord-length correspondence. The additional gate uses physical arc-length
weights, RMS error, signed normal bias, and the fraction of signed error on one
side. A materially biased candidate receives up to three passes through the
existing bounded Newton reparameterization path. The conservative continuous
maximum-error proof remains authoritative for every accepted line and cubic;
the user-facing 0.10 mm default and 0.08 mm internal fit budget are unchanged.

The diagnosed Coleman `P` bowl is OpenCV outer contour 27 from the exact
1,170 × 444 source at 80.0 × 30.358974 mm, manual threshold 122, no smoothing,
and 0.10 mm fitting tolerance. Source pitch is 0.0683760684 mm horizontally and
0.0683760676 mm vertically. Its 392 samples span source pixels
`(176.125, 327.125)` through `(195.875, 361.875)`. The four hard corners are at
samples 49 `(186.875, 339.125)`, 219 `(193.625, 354.875)`, 367
`(178.625, 327.125)`, and 380 `(176.125, 329.625)`; the protected neighboring
hard anchors are 48-50, 218-220, 366-368, and 379-381. No persistent straight
run is classified on this contour.

At the centering-only baseline, the old and new native sequence was
`LLCCCCLLCLLCLLC`; recursive splits remained
three and verified merges remain zero. The defect was inside the single cubic
from sample 220 to 366, not at an anchor, split, or merge. That span covers 147
threshold-boundary points across the lower transition, outer right arc, and top
transition. Previously it passed on its first chord parameters, so Newton never
ran: conservative max error was 0.076471 mm, nearest-point RMS was 0.035119 mm,
and mean signed normal error was -0.012450 mm (62.6% of samples on the inward
side). One Newton pass now precedes acceptance: conservative max is 0.066740 mm,
nearest-point RMS is 0.033027 mm, signed mean is -0.005640 mm, and same-side
fraction is 55.8%. Maximum error remains at source pixel
`(195.875, 341.625)`, parameter approximately 0.343, showing that the change
corrects correspondence within the long curve rather than moving its anchors.
Across the full contour, conservative max/RMS changed from
0.076471/0.031228 mm to 0.066740/0.027719 mm; independently projected
nearest-point max/RMS changed from 0.076252/0.027764 mm to
0.066449/0.026714 mm.

On the same prepared payload and Python 3.14 interpreter, five measured runs
after warmup changed the exact-fit median from **7.270 seconds** on starting
`main` to **7.668 seconds** with centering, a 5.5% increase. Useful quick
preview remained approximately **0.05 seconds** (0.051-second median after the
change) because it does not execute cubic fitting. Focused Coleman/synthetic
`P`/`D`, translated phase, rotation, shallow-arc, straight-`E`, rounded-`C`/`O`,
and existing fitter verification passes **39 direct tests**. The complete
focused raster/fitter/dialog/desktop selection passes **154 tests**. Broader
native-path, project/history, toolpath, planning, golden, digest/cache, and
desktop native-path verification passes **400 tests**, all with four xdist
workers. Repository Ruff, `python -m compileall -q laser_aligner`, and
`git diff --check` pass. This is automated Qt-free/offscreen verification only;
no interactive GUI or physical hardware test is claimed.

## Active raster-vectorization straight-edge recovery

The exact native fitter now classifies persistent straight source runs before
anchor selection on every contour, including contours that already have hard
corners. This fixes the Coleman stencil `E`, whose single detected hard corner
previously caused `_fitting_anchors()` to return before straight-run discovery;
the older independent 10%-of-perimeter minimum also excluded its 3.46 mm top
edge. Classification is orientation-independent and scale-aware: a candidate
must have material physical/source-pixel/oversampled extent, bounded full-run
orthogonal residual at the stricter of the fit tolerance and source
quantization allowance, and bounded local plus full-run directional change.
Nearby raster-step fragments merge only when the complete combined span passes
the same evidence. A classified span is continuously revalidated and persisted
as a native line. Without that positive evidence, a shallow curve remains a
cubic even when its chord alone is within the 0.08 mm internal fit budget.

The exact 1,170 × 444 Coleman development stencil at 80.0 × 30.358974 mm,
manual threshold 122, no smoothing, and the default 0.10 mm native fitting
tolerance produced a 2,038-sample canonical outer `E` contour. Its bounding box
was `(-18.316239, -1.598291)` to `(-13.068376, 6.076923)` mm, with one hard
corner at sample 335. The source-supported bottom (samples 59-275), top
(1397-1599), and merged left (1684-2021) runs measured 3.692308, 3.460072, and
5.774845 mm, with maximum chord residuals 0, 0.017009, and 0.017094 mm. The
persisted `E` changed from `LLCCCCCCCCCCCCCCCC` to
`LCLLCLCCCCCCLCLCLC`: straight source arms are lines while corner transitions
remain cubic. Its maximum validated fit error changed from 0.075076 to 0.075472
mm and remains below 0.08 mm. Rounded Coleman `C`/`O` regions, analytic rounded
joins, rotated straight edges, quantized/noisy rotated edges, and a shallow
0.05 mm-sag curve are covered explicitly so short curve plateaus are not
promoted to lines.

On the same prepared payload and interpreter, two independent five-run sets
after one warmup each measured a combined exact-fit median of **3.998 seconds**
at starting commit `19aa9bab` and **1.211 seconds** with the repair. Focused
verification passes **107 raster/fitter/dialog/desktop tests**; broader native-path,
project, toolpath, planning, golden, digest/cache, and desktop-workspace
verification passes **366 tests**. Repository-wide Ruff,
`python -m compileall -q laser_aligner`, and
`git diff --check` pass. No quick-preview authority, fitting tolerance, Newton
requirement, continuous proof, frame/extrema check, topology/clearance rule,
hierarchy rule, native persistence, project/history, planning/cache, G-code,
machine, motion, arming, or output-safety contract changed. This is automated
Qt-free/offscreen verification only; no interactive GUI or physical hardware
test is claimed.

## Active raster-vectorization responsiveness recovery

**Trace image to vectors…** now uses two bounded background stages. The first
decodes and displays the exact source, production foreground mask, and a
preview-only approximation of the extracted contour tree. The second ignores
that approximation, reuses only immutable prepared grayscale/mask/raw-contour
data for identical options, and runs the complete authoritative native
line/cubic fitter plus the existing continuous-error, frame/extrema,
self/adjacent-arc topology, compound-clearance, preview-flattening, and 4×
raster-hierarchy checks. The quick geometry has no native subpath or project/
planning conversion and cannot enable **Create vectors**. It is replaced by the
verified result only after exact completion. Quick and exact workers each
coalesce to the newest pending settings; stale results and cancellation cannot
create or replace project geometry.

The portable vectorizer exposes opt-in, non-persistent timing with elapsed time
and call counts for decode/preparation, mask generation, contour extraction,
corner classification, cubic fitting, Newton reparameterization, continuous fit
validation, adjacent merging, authoritative topology, preview flattening, and
raster hierarchy validation. Prepared white-composited grayscale is cached with
the verified source. The exact fitter hoists immutable derivative differences
out of Newton's point loop and avoids scalar `np.clip` overhead while retaining
current-main reduction order. The five-million-step continuous-validation
budget and proof are unchanged: Coleman profiling measured that proof at about
0.10–0.14 seconds, not as the latency bottleneck.

On the 1,170 × 444 Coleman development stencil at 80.0 × 30.358974 mm and
threshold 122, authoritative current `main` took **8.16–8.47 seconds**
unprofiled for the first/final result. The new core path measured **0.020
seconds** to verify/decode, **0.046 seconds** to the useful quick mask/outline
(**0.067 seconds** cumulative), and **5.96 seconds** for the background exact
fit (**6.03 seconds** cumulative). The normal dialog's 160 ms debounce puts the
expected first visual near **0.23 seconds**. The final Coleman native-geometry
JSON and metadata JSON matched starting commit `32bd1ec` byte-for-byte. No
fitting tolerance, corner rule, Newton requirement, continuous proof, topology
rule, hierarchy rule, native persistence, transform, project/history,
planning/cache, or output-safety contract changed.

Focused verification currently passes **144 raster/fitter/dialog/desktop
tests** and **235 native-path, project/history, planning, digest, cache, and
toolpath tests**. This is automated Qt-free and offscreen-widget verification
only; no interactive GUI or physical camera/controller/motion/laser test is
claimed.

## Active desktop Import and status-message layout fixes

The native File menu now exposes one **Import** submenu containing **SVG…**,
**G-code…**, **LightBurn project…**, and **Raster image…**. The existing import
actions remain authoritative, including their callbacks, icons, enablement, and
`Ctrl+I` / `Ctrl+Shift+I` shortcuts; only their File-menu grouping and displayed
labels changed. The old direct File-menu import entries are absent.

The bottom status bar now responds to every `QStatusBar.messageChanged` signal.
While a temporary message is active, it constrains the permanent job-progress
widget to its readable minimum and hides editing details plus runtime and zoom
readouts only as required by the available width. Active preparation/execution
progress remains the highest-priority permanent widget. Clearing or timing out
the message automatically restores the normal responsive labels. Messages that
cannot fully fit beside progress at the narrowest supported width are clipped
before the permanent widgets and retained in the status-bar tooltip rather than
painting over other status text.

This is presentation-only desktop behavior. It changes no import parser or
project transaction, planning, controller, motion, homing, arming, laser output,
camera service, machine authority, or project geometry behavior. Verification
passed **58 focused tests** across the real menu/action construction, responsive
status geometry, control-surface source contract, reusable import review, and
all four desktop import integrations. Repository-wide Ruff,
`python -m compileall -q laser_aligner`, and `git diff --check` passed. This is
automated offscreen-widget coverage only; no interactive GUI or physical
hardware test is claimed.

## Active Objects layer-color swatch recovery

The Objects table again presents each assigned operation layer as a visible
24 px color button beside the layer name. The exact earlier implementation was
recovered from local branch/worktree commit `ebfac234`; the native Bézier branch
had been independently rebased onto merged raster-fit work and never included
that sibling UI commit, leaving the common ancestor's static 12 px icons in
place. Only the layer-color portion was restored; the recovered commit's
unrelated File-menu grouping was not copied.

The button sends its row's assigned layer ID to the existing `LayerPanel` color
chooser. Cancel emits no edit. A valid choice continues through the queued
`layerEdited` signal, `E3MainWindow._layer_edited()`, and `UpdateLayerCommand`,
so one undoable shared-layer change refreshes every Objects swatch, the
Cuts/Layers table and selected color control, the bottom palette, and workspace
vectors. Object selection and layer assignment remain independent, and speed,
power, passes, output state, visibility, scan settings, and power-correction
settings are preserved. Raster IMAGE and native cubic PATH rows use the same
assigned-layer control; raster-vectorization preview-overlay colors remain
separate and unchanged.

Focused Windows/offscreen coverage passed **64 tests** across Objects/Cuts,
layer-edit routing and history, native/raster workspace rendering, and raster-
vectorization UI. The complete Windows Python 3.14 suite passed **2,591 tests**
with **14 expected platform/capability skips**. Repository-wide Ruff,
`python -m compileall -q laser_aligner`, and `git diff --check` passed. This is
automated offscreen-widget verification only; no interactive GUI, camera,
controller, motion, homing, arming, laser-output, or physical test was performed
or is claimed.

## Active remove-simulation-mode milestone

Production simulation has been removed as a runtime and user capability. The
current configuration model and packaged default template accept only the real
serial-family transport boundary; first-run requires an explicitly saved real
machine and exits when canceled. `AppContext` constructs only local or remote
real camera services. Missing controllers and cameras remain offline and expose
their real errors rather than producing fake success or image state.

The former controller peer and transport live under `tests/fakes/` and are
injected only by tests. The product package no longer contains simulator camera
or generated-frame helpers, generated/frozen camera UI, or a camera test-frame
API. Legacy `simulation: false` configuration is normalized away. Before any
desktop runtime, credential, camera, or controller service is constructed,
`simulation: true`, a legacy simulator backend, an active saved simulator, or a
simulator-only registry opens an explicit recovery wizard. Recovery initially
selects nothing: the operator must select a configured physical machine or
create a new safe physical snapshot. Finish atomically replaces configuration,
retires simulator records with an exact one-time backup, and rolls back the
configuration, registry, credential, backup, and completion marker together on
failure. Cancel leaves those files untouched and exits without constructing a
runtime. Inactive simulator entries beside an active physical machine retain
their existing automatic atomic retirement behavior.

Normal recovery from a legacy configuration in the replaceable application
directory writes the repaired configuration into the upgrade-preserved user
state; an explicitly supplied `--config` is repaired in place. Both destinations
are stale-checked before the transaction writes. Both browser and desktop entry
points now construct `CoreRuntime`, whose active
saved-machine snapshot is the controller/work-area/laser and running-identity
authority passed to `AppContext`. The packaged and Python controller-port
defaults use the explicit `SELECT_CONTROLLER_PORT` sentinel. With no saved
registry, that sentinel and the former implicit `/dev/ttyUSB0` default are setup
errors; E3 does not create a plausible machine from either. An explicitly saved
physical `/dev/ttyUSB0` endpoint remains valid. Project, machine-registry, and
material schemas remain unchanged.

Every normal browser and desktop product entry point now grants process hardware
authority unconditionally. The browser parser no longer exposes `--hardware`,
and the normal shell/service entry points require no mode flag. The longstanding
`run-hardware.sh` and `run-desktop-hardware.sh` filenames remain only as exact
aliases to their normal launchers for compatibility; they cannot select a
different runtime. Desktop installation creates one normal application entry.
Hardware capability does not eagerly connect, move, Home, arm, or emit output;
`machine.allow_motion`, coordinate trust, preflight, exact-program authority,
temporary arming, bounds, STOP, and `M5` remain unchanged. Dedicated Pi
controller-owner services retain their separate explicit hardware gate.

Focused simulator-recovery, saved-machine authority, desktop startup, CLI,
camera-boundary, configuration, and first-run verification passed. The final
launch-authority follow-up passed **104 focused CLI/runtime/launcher tests** and
**284 focused machine-safety/runtime tests** across the internal hardware gate,
real disconnected-controller failures, strict GRBL/Marlin transcripts and
dialects, transport selection, and saved-machine authority. The complete
Windows Python 3.14 suite passed **2,352 tests** with **14 expected platform
skips**.
Repository-wide Ruff, `compileall -q laser_aligner`, and `git diff --check` also
passed. No physical controller, camera, motion, arming, or laser-output test was
performed or is claimed; unavailable hardware remains honestly offline while a
valid saved machine retains offline authoring and project editing.

The desktop startup-order follow-up inspects an explicitly supplied legacy
configuration named `default.json` before classifying any configuration as the
packaged first-run template. The actual packaged template still enters ordinary
first-run when no preserved configuration exists, and the configuration written
by first-run is inspected again before bridge credentials or `CoreRuntime` /
`AppContext` construction. Canceling either setup path exits before runtime
construction. Focused simulator-recovery and first-run verification passed **36
tests**. The complete Windows Python 3.14 suite passed **2,358 tests** with **14
expected platform skips**; repository-wide Ruff, `compileall -q laser_aligner`,
and `git diff --check` also passed. No physical hardware test was performed or
is claimed for this startup-only correction.

## Active native cubic Bézier path foundation

Project schema 3 persists PATH/POLYGON geometry as one validated, Qt-free native
representation containing line and cubic segments, open or closed subpaths,
compound paths, and an explicit `evenodd` or `nonzero` fill rule. Coordinates
remain normalized in object-local space while the existing `Transform` remains
the authority for size, both mirrors, rotation, and translation. Schema-1 and
schema-2 `geometry.polylines` load as equivalent native line-only subpaths in
memory; opening an old file does not rewrite it, and the next explicit save
writes only canonical schema-3 native geometry. Existing SVG, G-code, and
LightBurn polyline constructors pass through the same immediate compatibility
conversion and retain no second legacy geometry copy. Schema 3 is intentionally
forward-incompatible with older E3 builds that understand only schema 2; those
builds reject it rather than silently losing native path data.

The workspace builds `QPainterPath` line and cubic elements directly, applies
the persisted fill rule, and does not flatten native curves for display. Native
objects continue through project cloning, add/replace/delete, duplication,
grouping, layer assignment, object-level transforms, save/reopen, recovery, and
undo/redo as native geometry. Individual anchor/handle editing and native SVG
curve preservation remain follow-up work.

The native Objects panel exposes **Trace image to vectors…** only when exactly
one imported IMAGE object is selected. Its window-modal Raster Vectorization
dialog shows the original raster, generated foreground mask, and vector overlay.
Magenta, cyan, yellow, white, and black presets plus 0–100% opacity are
preview-only and repaint locally without rerunning fitting or changing geometry,
metadata, layer power, or output authority. Automatic Otsu, manual 0–255, and
usable-alpha detection are available with inversion, alpha cutoff, physical
minimum-feature area, smoothing, a millimetre fitting tolerance, outer-only or
full hierarchy output, and Replace or Keep/optionally-hide source handling.
Preview work is debounced and coalesced independently for one quick and one
exact worker plus the newest pending settings. The quick mask/outline remains
visible while exact verified fitting continues; only the verified result can
enable creation. Cancel performs no project mutation.

Imported-raster vectorization retains its fitted straight/cubic result instead
of destroying curves at the former `_fit_and_flatten_contour()` seam. The stages
canonicalize the complete closed-contour cycle, classify only corners persistent
across physical scales, add generic straight-run anchors, fit bounded line/cubic
segments with shared tangents at non-corner joins, validate/convert that fit to
one authoritative native subpath, reject exact cubic extrema outside the
image-local frame, and derive bounded preview points from that native geometry
without clipping. Bounded exact cubic self/adjacent-arc checks and adaptive
physical-space clearance prove the authoritative contour forest before the
existing 4× rasterized hierarchy comparison. Source- and 4×-resolution budgets
reject pathological masks before full allocation. Parent/depth/hole state and
quality metrics remain attached to each native subpath; no fitted cubic is
replaced by persisted polyline samples.

The raster fitter now incorporates only the compatible quality ideas from
historical reference commit `4310769`; that commit was neither merged nor used
as a whole-file source. Current-main canonicalization, physical-distance/
tolerance-aware corner classification, three-sample hard-corner anchoring,
generic straight-run anchors, shared non-corner tangents, frame-constrained
handles, exact extrema checking, native self/adjacent-arc topology, compound
clearance, hierarchy validation, native persistence, preview flattening, and
planning/cache identities remain authoritative. Inside those boundaries,
positive tangent-constrained cubic handles are refined by bounded Newton
reparameterization. Every accepted line/cubic receives a conservative
continuous proof against its target polyline using the convex hull of the
corresponding difference cubic, so a candidate that agrees only at stored
samples but forms a between-sample lobe is split. Adjacent like-kind pieces
merge only after a fresh fit proof and the current adjacent-native-arc check
pass. A separate 5,000,000-step fit-validation budget bounds that work.

Contour and metadata diagnostics now record conservative maximum fit error,
sampled mean/RMS fit error, fit-validation sample count, current-classifier hard
corners, recursive splits, verified merges, and the longest smooth fitted span;
the review dialog exposes the most useful values. Historical index/span corner
classification, historical frame/topology substitutes, the implicit
source-pixel trace-cleanup target and retry loop, the historical 0.01 mm default,
and its alternate preview/fit-error boundary were rejected as obsolete or as
unproven against current compound-contour safeguards. The current 0.10 mm
default, explicit user smoothing, current preview contribution to maximum
estimated deviation, and all downstream safeguards remain unchanged.

One compound native path preserves all selected outers and holes in the original
image-local frame with the source Transform and SHA-256. Replace/Keep/hide,
vector insertion, and any safe-layer creation are one undoable command. The
active layer is reused only when it is a visible Line layer at 0% power with
output disabled; otherwise E3 creates a visible `<image name> trace` Line layer
with the same safe defaults. The new vector and layer are selected, a retained
source remains below the vector, and its Transform is preserved. The ordinary
editable layer color remains independent of preview-overlay styling.

`object_polylines()` is the single native-curve-to-planning boundary. It applies
the complete object transform to anchors and controls first, then performs
deterministic recursive de Casteljau flattening in physical project millimetres
at **0.025 mm** tolerance. The normalized-geometry planning stage is version 2;
its dependency identity includes the tolerance and flattening algorithm version
so a stage-1 artifact cannot be reused. Downstream placement, containment,
preview, preflight, and G0/G1 generation continue to consume ordinary
`Polyline` values. Generation applies one aggregate 250,000-point normalization
budget across fresh LINE/FILL/RASTER vectors and normalized-cache hits before
publishing downstream artifacts. Closed compound subpaths must be separated by
more than the sum of their per-subpath curve envelopes plus a scale-aware numeric
margin; all-line subpaths carry no curve envelope. No controller spline or arc
command is introduced.

Before output acceptance, exact derivative-root extrema bound complete cubics in
rectangular work areas. Convex guarded polygons use recursive Bézier subdivision
and the convex-hull property, including the 0.025 mm flattening envelope. Those
checks are applied in project-local, placed machine, and controller-offset
domains in addition to the existing flattened-path checks. Compound fill
planning honors even-odd parity or deliberate nonzero winding; containment cut
ordering remains winding-independent and deepest-first. Cubic bounds are applied
per cubic, so an exact boundary line is not spuriously expanded merely because
another subpath contains a curve.

Native-path production caps are 8 levels of JSON nesting, 8,192 subpaths per
object, 100,000 segments per object, 250,000 segments per project, 250,000
flattened output points, 18 recursive subdivisions, and coordinate magnitude
1,000,000. Shape-history execute/undo/redo validates replacement-aware project
segment totals before mutation, and failed commands preserve both document and
history state. Existing raster caps remain 67,108,864 pixels in the 4× workspace,
4,096 retained connected components, 8,192 contours, 1,000,000 raw
pre-simplification points, 100,000 fitted segments, 5,000,000 bounded continuous
fit-validation steps, and 250,000 transient preview-flattened points. Limit
failures recommend simplifying or cleaning the source artwork.

This remains offline authoring and guarded planning behavior. It does not
connect, Home, move, arm, enable output, generate a job automatically, or start
execution. `machine.allow_motion`, coordinate/reference trust, exact program
authorization, temporary arming, Preview, preflight, START JOB, STOP/immediate
`M5`, controller dialects, and offline editing remain unchanged. Verification is
automated Qt-free and offscreen-widget geometry coverage only. The requested
post-rebase focused integration run passed **457 tests**. The Coleman stencil
diagnostic used the production automatic threshold (**Otsu 122**), 80.0 mm
width, 30.358974 mm height, no inversion, alpha cutoff 1, 0.05 mm² minimum
feature area, no smoothing, 0.10 mm fitting tolerance, and all contours. Its two
target rounded bars each retained a `CLCCLC` six-segment native sequence; their
maximum fit errors were **0.033948 mm** and **0.039448 mm**, their worst
non-corner join-angle discontinuities were **0 degrees**, and their centered
geometric difference was **0.038244 mm**. The complete Windows Python 3.14.4
suite passed **2,588 tests** with **14 expected platform skips**; repository-wide
Ruff, `compileall -q laser_aligner`, `git diff --check`, and
`git diff --check origin/main..HEAD` also passed. No controller, camera, motion,
homing, arming, laser-output, physical tracing-quality, or physical accuracy
verification is performed or claimed.

The historical-fitter consolidation passed **93 focused tests** across raster,
fitter, dialog, and desktop integration plus **311 native-path, topology, frame,
project/history, preflight, planning-stage, digest, cache, and golden tests**.
The complete Windows Python 3.14.4 suite passed **2,599 tests** with **14 expected
platform/capability skips**. Repository-wide Ruff,
`python -m compileall -q laser_aligner`, and `git diff --check` passed. This is
automated Qt-free and offscreen-widget verification only; no interactive GUI,
camera, controller, motion, homing, arming, laser-output, physical tracing-
quality, or physical accuracy test was performed or is claimed.

## Active Windows updater hardening

The packaged Windows updater now crosses an explicit external-process boundary
before launching the verified Inno Setup executable. E3 temporarily restores
standard Win32 DLL resolution, filters only `sys._MEIPASS`-rooted entries from a
copied child `PATH`, creates a detached process with the existing explicit Inno
arguments and installer-directory working directory, and then restores its own
DLL search state. Successful process creation is authoritative: if restoring
the dying parent's DLL state fails after the child exists, E3 logs that parent
cleanup failure and completes the handoff instead of claiming the installer did
not start. After the normal unsaved-project approval, the desktop starts the
same bounded shutdown used by an ordinary Close. Once close preparation is
accepted, the verified installer is spawned before synchronous runtime teardown
rather than waiting indefinitely for worker ownership to drain; late task
publication is already suppressed and the shared four-second process deadline is
active. A
process-creation failure after accepted terminal shutdown shows
a standalone error containing the verified installer path for manual launch,
then exits rather than presenting the stopped desktop as usable.

After rebasing this updater work onto the completed simulation-removal main,
focused updater, installer, offscreen Qt handoff, and real MainWindow drain
coverage passed **29 tests**. Focused no-simulation/runtime-authority,
`MachineService`, and exact controller-transcript regressions passed **61
tests**. The complete Windows Python 3.14 suite passed **2,368 tests** with **14
expected platform skips**; repository-wide Ruff, `compileall -q laser_aligner`,
and `git diff --check` also passed. The original installed PyInstaller
`--windowed --onedir` E3-to-visible-installer scenario has not been repeated and
is not package-verified. No physical controller, camera, motion, arming, or laser
verification was performed or is claimed.

## Active desktop layout v7 refactor

The desktop's default layout now keeps the canvas open to the status bar instead
of reserving a lower G-code/job area. Cuts, Camera, Objects, Shape, Templates,
Trace, Machine, and Material Recipes share one right-hand tabbed sidebar.
Template and Trace generation controls live beside their respective creation
workflows, while the persistent runtime strip owns Connect/Reconnect,
Disconnect, the deliberately disabled Pause control, and software STOP. Job
preparation, execution, and finishing progress is always represented in the
global status bar. At compact widths the status bar preserves progress first,
then exposes runtime, zoom, and edit details only as space allows; omitted
runtime detail remains available in its tooltip. Saved desktop geometry/state
is version 7 so older opaque dock layouts cannot recreate the removed panels.

The corrected workspace Live Overlay now offers nominal 0.5, 1, 2, 4, 5, 10,
and 15 fps selections and defaults to 2 fps. Its controller timer accepts the
nearest integer 15 fps period of 67 ms. Only one corrected-frame task may be in
flight: periodic ticks are dropped while it runs, and repeated explicit refresh
requests coalesce into at most one pending replacement. Slow correction or
network delivery therefore reduces the displayed frame rate instead of stacking
work. This is separate from the raw Live Monitor and camera capture rates.

The active UI work is rebased directly on `origin/main` commit
`e079f37b7b3249ff30b95a99f30fc7199921ea8a`, including PR #36's rule that an
offline camera reports stale bed-map status without opening the Bed Mapping
Required modal. Remote camera status parsing also tolerates the obsolete field
from a legacy physical Pi node only when it is exact boolean `synthetic: false`.
The returned mapping is copied before that field is removed; boolean `true`,
integer `0`, and every other value fail closed. Current fields are passed to
`CameraStatus` unchanged, the source mapping is not mutated, and no simulation
capability is restored.

The layout portion is presentation/action routing only, and the overlay-rate
change is camera scheduling only. The existing Preview, preflight, exact-program
authority, temporary arming, `MachineService`, guarded execution, STOP, and `M5`
boundaries are unchanged. Offscreen production-theme
layout checks covered 1600, 1080, and 900 px windows. A genuine 13 pt font audit
at 900 and 1080 px covered preparation, execution, every finishing state, status
containment, and mapped runtime-toolbar containment; Connect, Disconnect, Pause,
and STOP remained ordered and fully visible. Focused corrected-overlay,
controller scheduling, desktop layout, and async coverage passed **193 tests**.
Focused offline-camera coverage passed **9 tests**; remote-camera/status coverage
passed **65 tests** with **2 expected Linux-only V4L2 skips**; and PR #36 camera
provenance coverage passed **64 tests**. The complete Windows Python 3.14 suite
passed **2,402 tests** with **14 expected platform/privilege skips**.
Repository-wide Ruff, `compileall -q laser_aligner`, and `git diff --check`
passed. No interactive GUI, real camera, physical controller, motion, arming, or
laser-output test was performed or is claimed.

## Active Pi-owned remote job execution

Normal `e3bridge://` machine jobs now use the explicit high-level
`E3MACHINE/2` protocol and never silently fall back to the incompatible legacy
`E3BRIDGE/1` raw serial bridge. Windows `RemoteMachineService` owns exact program
preflight, bounded 64 KiB chunk upload, FINALIZE, operator START authorization,
monitoring, reconnect, and explicit STOP. The combined Pi node owns a persistent
`PiJobStore`, one `PiJobService`, and exactly one local `MachineService`/serial
session. Direct local serial continues through the original desktop
`MachineService` path.

Upload and START are separate. The Pi maps a client-generated canonical UUID to
a server-owned `.part` path, fsyncs bounded chunks, checks the declared
size/SHA-256 and strict UTF-8, runs independent local program validation,
journals the commit, and atomically renames only a verified job to `.gcode`.
FINALIZE binds the exact program digest, guarded output polygon, motion/power
flags, explicit GRBL/Marlin dialect, and current execution-policy digest. START
hashes and preflights the committed bytes again, writes `starting`, then performs
Pi-local connect/Home/park/arm/start. The later durable
`ownership_accepted = true` plus `start_accepted_at` transfers execution
ownership; the subsequent START response only reports that fact. After START is
sent, a lost or failed response is ownership-uncertain and is recovered by
querying that UUID; START is never retried blindly.

After acceptance, loss of Windows, Wi-Fi, TCP, or a monitoring client has no
controller effect and no heartbeat is required. The Pi locally streams one
command and waits for its acknowledgement before persisting progress; normal
powered completion still performs `M5`, planner barrier, Home, park, step-idle
restoration, and motor release. Explicit STOP halts further streaming and
attempts the configured controller stop plus `M5`. Detected controller
error/alarm, serial write/read failure, ACK timeout, corrupt stored data, or
runner/completion failure halts further streaming, attempts a best-effort `M5`,
invalidates controller trust as applicable, and records a terminal result while
the Pi service remains alive. STOP bypasses ordinary command/store serialization.
While active, another START, connect/reconnect/disconnect, Home/park, jog, manual
command, calibration motion, realtime sample, and stepper hold are rejected;
status and STOP remain allowed.

The durable states are receiving, prepared, starting, running, stopping,
complete, failed, stopped, and interrupted. A Pi process restart converts any
persisted active state to interrupted and never restores execution authority or
auto-resumes. This does not guarantee that a sudden process/power failure can
deliver software cleanup to an independently powered controller; physical
E-stop/interlock authority and operator attendance remain mandatory. Retention
is bounded to eight metadata records and the latest two terminal G-code files,
with receiving/prepared/active artifacts protected and stale `.part` files
cleaned after 24 hours. Job size is capped at 64 MiB, frame payload at 128 KiB,
and client paths are never accepted.

Protocol/store/server/desktop tests cover authentication and counted HMAC frames,
incompatible versions, traversal/malformed metadata, atomic crash recovery,
complete/partial/wrong uploads, duplicate FINALIZE/START, accepted-client
disconnect through complete remaining execution, prepared/upload disconnect,
reattach with exact persisted in-flight ACK progress, completed-offline discovery,
ordinary remote desktop shutdown with an empty observer cache, priority STOP
during ACK wait, all specified controller/serial failures, reboot interruption,
active-operation blocking, camera-client independence, local execution
preservation, and one real
`RemoteMachineService → E3MACHINE/2 → PiMachineServer → PiJobService →
MachineService` socket stack. After merging the complete Camera Trace
appearance-veto and exact Exposed-bed/4× Mask display work, the combined focused
Trace/Pi batch passed **383 tests** and the complete Windows Python 3.14
four-worker suite passed **2,843 tests** with **14 expected platform/privilege
skips**. Repository Ruff, bytecode compilation, and diff checking passed. No
interactive desktop, physical Pi, physical controller, motion, laser output,
process-kill recovery, or real network-disconnect test was performed; those
remain required before physical deployment and hardware acceptance.

## Current repository validation and deferred package check

Fast Development CI runs Windows Python 3.12 Ruff, desktop dependency/bytecode
validation, and the complete desktop-enabled suite with four bounded workers for
`fix/**`, `feature/**`, `agent/**`, `cleanup/**`, and `architecture/**` pushes.
Compatibility CI runs serial pytest on Windows Python 3.10 without desktop
extras and Windows Python 3.12 with desktop extras, plus repository Ruff, for
`main` pushes, pull requests targeting `main`, and manual dispatch. Direct
Linux/Pi components retain focused verification when changed; there is no
standing Ubuntu compatibility matrix.

The installed frozen PyInstaller `--windowed --onedir` E3-to-visible-Inno
handoff remains intentionally package-unverified because no disposable
interactive Windows environment was available. No lab installer or certificate
was applied to the development host, and the public update channel was not used
for the deferred exercise. This does not change the automated updater evidence
or any hardware, motion, arming, coordinate, bounds, preflight, STOP, or `M5`
authority.

## Historical verification record

The entries below record earlier milestones and may mention product simulation
that existed at the time. Those statements are historical evidence, not current
operator guidance or current functionality.

The desktop machine-configuration workflow is now generic across the existing
simulator, GRBL, and Marlin profiles without adding a controller dialect or a
second profile model. `MachineProfile` remains reusable motion-platform and
controller defaults, `ToolHeadProfile` remains reusable laser/tool defaults,
and each `MachineInstance` remains the operator's complete validated saved
snapshot. Machine Manager exposes the existing backend, protocol, endpoint,
timing, work-area, feed, Home/release/photo, laser, framing, offset, and guarded-
output values. Serial fields and GRBL-only step-idle settings are conditional.
Creating from profiles is distinct from editing a saved instance; neither
operation connects, Homes, jogs, arms, moves, emits, or executes work.

Machines created or duplicated through Machine Manager begin without another
machine's camera, calibration, or honeycomb-span binding, and profile-created
machines retain the existing safe-off motion/default-power/frame defaults.
Machine Setup names the immutable running machine and its machine/tool profiles,
reports current versus saved optical/calibration binding state, and offers an
explicit persistence-only action to bind the active optical profile to that
saved machine for a future launch. This action does not mutate the current
`CoreRuntime` identity or grant calibration, coordinate, motion, or output
authority. Until a matching bound profile is actually running, foreign
honeycomb support is not used as the current authoring/execution frame.

First-run onboarding now defaults to the software simulator and can instead
create a concrete saved machine from any existing physical machine profile plus
an existing physical tool-head profile. The selected schema-1 registry snapshot
is used on launch, while motion, default/frame power, and low-power framing stay
off and camera/calibration bindings stay empty. The optional network test checks
reachability only; saving performs no controller or camera action and is not
physical verification.

Normal new projects now resolve operation defaults from the immutable **running**
machine/tool profile identity, never the next-launch registry selection or
arbitrary user SQLite recipes. The curated priority is exact machine+tool, then
future explicit tool-only, then future universal records, with no tier mixing.
The existing Ender-3 S1 Pro plus generic 10 W identity retains all 13 historical
operations exactly. Other current combinations receive one visible Line layer
at `min(1000, running max work feed)` with 0% power, one pass, output disabled,
zero corrections, no air-assist assumption, and a visible instruction to
configure or apply a compatible recipe. `default_operation_layers()` and the
project/material/machine registry schemas remain unchanged. Project bounds and
machine-versus-honeycomb coordinate selection still come only from the actual
running work area and currently bound support.

Focused verification for this milestone passed **886 tests** across saved
profiles and runtime resolution, Machine Manager and Machine Setup, first-run,
material recipes, historical and resolved project defaults, coordinate/support
authority, structured preflight, `MachineService`, strict controller
transcripts, dialect/transport selection, deterministic planning goldens,
desktop async lifecycle, toolpath generation, and job planning. Repository-wide
Ruff, `compileall -q laser_aligner`, and `git diff --check` also passed. This was
automated Windows Python 3.13 verification only; no physical machine,
controller, camera, motion, arming, or laser behavior was re-verified or newly
claimed by this configuration/authoring change.

The machine core now has explicit transport and controller-dialect boundaries
without changing controller support or execution authority. The neutral
`MachineTransport` protocol remains limited to open, close, raw/line writes,
line reads, and drain. One construction-only `create_machine_transport()`
factory maps the existing saved `backend`, `port`, and `baudrate` values to a
fresh simulator, local POSIX serial, or authenticated `e3bridge://` network
transport. Bridge URI selection still happens before the local-platform gate,
so the network transport remains available on Windows while unsupported local
POSIX serial continues to fail clearly. Concrete network and POSIX imports stay
lazy, and the former `machine.serial_backend` protocol and factory imports remain
available for compatibility. No `machines.json` or configuration schema changed.

Current GRBL and Marlin command/parsing policy now lives in immutable,
Qt-neutral `ControllerDialect` values and a deterministic registry. Dialects
describe pure semantics: stable IDs, identity recognition and probes, response
classification, query/home/barrier/release/stop command policy, and existing
GRBL status, position, coordinate, and session parsing. They cannot open or
write a transport, acquire service locks, authorize output, or start work.
`protocol = auto` retains the existing startup delay and drain, GRBL-banner
recognition, ordered `$I` then `M115` probes, 1.0/1.5-second probe timeouts,
accepted responses, and fail-closed result; it sends no additional commands.
The simulator transport now delegates the same state and response behavior to a
separate in-process simulated-controller peer, leaving its transport surface as
communication mechanics while preserving its import path and observable test
state.

`MachineService` remains the sole normal safety, authorization, and orchestration
authority. It still decides when probing and writes are allowed and retains the
hardware gate, motion gate, temporary arming, program-digest authority, guarded
stream validation, command/ACK ownership, STOP epochs, cancellation, uncertain-
state/reconnect handling, job lifecycle, and all best-effort `M5` paths. A saved
machine profile continues to describe the physical motion platform and its
transport/controller settings; a tool-head profile continues to describe the
laser/tool configuration. Neither profile, a dialect, nor a transport grants
execution authority, and no stable profile IDs changed.

Focused machine-boundary verification passed 507 tests with the six expected
Windows skips for POSIX pseudoterminal/`termios` cases. That selection covered
MachineService, immutable dialects, strict GRBL/Marlin transcripts, the
simulator peer, transport selection, local/network/bridge behavior, saved
machine profiles and runtime resolution, structured preflight, deterministic
planner goldens, reconnect/runtime-strip behavior, Machine Setup/Manager, and
desktop async job lifecycle. After final parity-audit additions, the complete
MachineService/dialect/factory/transcript subset passed 269 tests. Repository-
wide Ruff, `compileall -q laser_aligner`, and `git diff --check` also passed.

An identical synthetic immediate-ACK loop measured a median 540.32 ns per ACK
before and 578.28 ns after the refactor: about 37.96 ns of policy-dispatch cost
per acknowledged line, with no registry lookup in the stream loop. That
CPU-only difference is negligible beside controller I/O and does not introduce
a generic execution framework. This refactor has automated verification only;
no physical GRBL or Marlin controller behavior was re-verified, and no new
machine/controller compatibility is claimed.

The existing SQLite `MaterialPreset` / `MaterialDatabase` system is now a
machine-aware material-recipe authoring library rather than a parallel preset
model. Recipes include the complete controlled `OperationLayer` settings,
optional stable motion-platform/tool-head profile scope, and an optional
recommended color. Compatibility is Qt-neutral and deterministic: exact
machine/tool, tool-only, universal, or incompatible by exact IDs only. The
desktop prioritizes compatible recipes, keeps incompatible rows visible for
custom CRUD, disables their Apply action, and refreshes against the immutable
running profile identity when the surrounding machine/profile UI changes.

Applying a compatible recipe performs one undoable `UpdateLayerCommand`. It
does no power/speed scaling, creates no geometry, and preserves the operation's
ID, authoring name, visibility, priority, and current output-enabled state; an
optional recommended color is the only authoring identity it may replace.
Recipes are not stored in `.e3laser` projects and grant no output authority.
Structured preflight and exact planning continue to inspect the resulting
ordinary layer values, and hand-edited layers remain supported. No controller,
motion, arming, stop, laser, G-code, JobPlan, project-schema, or execution path
changed.

The material database now uses an explicit in-place schema migration. Existing
row IDs and values are copied transactionally, old rows become universal,
new fields receive deterministic defaults, scope-aware rows with the same
material/name/thickness can coexist, and reopening is idempotent. Default
seeding is insert-only and cannot overwrite newer user data. The 13 existing
operator-supplied E3 10 W new-project operations and their exact-profile
built-in recipes derive from one curated value source while preserving every
historical layer field, order, color, correction, visibility, and output value.

Focused verification for this authoring/database increment passed 480 tests:
material/database migration and CRUD, complete default-layer parity, recipe
authority, structured preflight, deterministic planner goldens, toolpaths,
MachineService regressions, desktop material/layer integration, the real
startup-drained no-hardware action gate, dock wiring, and the full asynchronous
job-preflight desktop file. Repository Ruff, compileall, and diff checks also
pass. This increment requires no physical controller, motion, camera, arming,
or laser test.

The desktop project pipeline now has a Qt-neutral **structured job preflight**
before exact toolpath generation. `PreflightSeverity`, `PreflightFinding`,
`PreflightCounts`, and `JobPreflightReport` expose deterministic dotted finding
codes, structured context, severity counts, and one derived ready/blocker result.
`build_job_preflight_report()` inspects a detached `ProjectDocument` snapshot
against a detached `JobPreflightContext`; it does not flatten vector geometry,
decode raster pixels, build G-code, create Qt objects, contact a controller, or
change machine authority.

The report projects existing preparation rules rather than defining a second
planner. It covers project-versus-machine work-area agreement, machine versus
honeycomb-local coordinate authority, execution-grade support/frame and output-
polygon binding, calibration-profile identity, read-only bed-calibration
validity and honeycomb-support CURRENT state, layer/object/output eligibility,
operation-setting validity, configured machine work/travel feed ceilings,
bounded raster headers and aggregate resource limits, and known execution-
readiness facts. A stale bed calibration or support state blocks honeycomb-local
generation. Only provably exact local bounds for unrounded rectangles and valid
two-point lines become structured bounds blockers. Rounded rectangles, ellipses,
images, paths, and other complex geometry remain deferred with vector
flattening, fill/raster construction, placement, laser-spot correction, final
bounds, exact raster identity/decode, stream construction, and command
validation to the authoritative planner and guarded machine path.

Desktop Generate now runs snapshot, structured preflight, exact planning, and
Preview preparation under the existing owner-tokened asynchronous lifecycle. A
blocking report stops before `generate_project_gcode()` and opens a reusable,
non-modal structured findings dialog after releasing preparation ownership.
Ready and warning-only reports continue into exact planning and are embedded in
the window-modal exact Preview; warnings remain visible and non-blocking. The
view bounds rendered findings and structured context without truncating the
immutable report. STOP, project replacement, revision changes, and authority
changes retain their existing cancellation/stale-result behavior.

Focused Windows Python 3.13 verification passed **57 tests** across the core
report, reusable offscreen Qt view, embedded Preview, and planning-cache
contract; **64 tests** across the full desktop asynchronous job lifecycle;
**159 tests** across toolpath, JobPlan, staged planning, dependency digests,
cache behavior, and deterministic planning goldens; and **251 tests** across
raster assets, coordinate audit, calibration provenance/profiles, and
`MachineService` safety. Repository-wide Ruff, `compileall`, and final diff
checks are clean. This increment has no interactive GUI or physical controller,
motion, camera, arming, or laser verification and does not change their
authority or behavior.

The planner now has a behavior-preserving typed-stage spine for LINE output:
`SceneRevision -> NormalizedGeometryArtifact -> OperationArtifact ->
PlacedGeometryArtifact -> ControllerGeometryArtifact -> EncodedProgramArtifact
-> immutable JobPlan`. `SceneRevision` carries a canonical project-content
digest and the normalized, placed, and controller LINE artifacts carry
versioned dependency digests separate from their run-oriented artifact IDs.
`E3MainWindow` owns one bounded, lock-protected, in-memory `PlanningCache`
across exact background planning requests. Unchanged normalized geometry,
machine-beam placement, and controller geometry can therefore be reused while
fresh artifact metadata and the normal local/placed/controller safety
validation still run for every request. Operation wrapping, raster/fill
planning, encoded-program generation, and immutable `JobPlan` construction
remain uncached. The deterministic planning goldens remain unchanged.

Focused cache/planning verification passed **150 tests** with Ruff, compileall,
and diff checks clean before the benchmark-only addition. A repeatable Windows
benchmark using 8 LINE layers x 32 objects (256 objects), seven measured runs
per scenario, initially reported 325.598 ms median uncached generation and
317.806 ms warm identical regeneration. Cache hit/miss counts matched the
dependency model exactly, but warm identical planning improved only about
**2.4%**, showing that LINE geometry recomputation was not the dominant cost.
Profiling then identified repeated JobPlan G-code parsing as the actual hot
path. Two behavior-preserving follow-up changes first reduced each executable
JobPlan line from three word parses to one and then added a low-allocation scan
path that keeps the shared G-code regex and numeric semantics while avoiding
temporary `GcodeWord` objects and duplicate comment stripping in
`build_job_plan()`. The same 256-object benchmark improved to 221.688 ms median
uncached after the single-parse change and then to 199.619 ms after the
low-allocation scanner, with warm identical regeneration improving from
317.806 ms to 215.142 ms and then 195.586 ms. A further experiment that moved
JobPlan summary aggregates into the per-move loop produced no measurable
improvement and was rejected rather than merged. Routine optimization of this
roughly 0.20-second synthetic planning case is therefore paused; future
performance work should be driven by a user-visible slow workload such as a
large imported vector/raster job, not by chasing smaller benchmark-only gains.
These internal planning changes are automated-test and local-benchmark verified
only; no physical controller, motion, camera, or laser test was required.

The project layer now has a Qt-neutral **Importer Manifest / Registry**
foundation for foreign-file discovery before source content is committed into
an E3 project. `ImporterSpec`, `ImportCapability`, `ImportLayerManifest`,
`ImportScanManifest`, and `ImporterRegistry` provide immutable importer identity,
case-insensitive suffix lookup, file-size limits, capability declarations,
natural-size/layer scan facts, review warnings and approximations, and explicit
blocking errors or unsupported features. File manifests also carry the SHA-256
of the exact scanned source bytes. The default registry describes SVG, raster
images, LightBurn, and bounded foreign G-code using shared supported-suffix,
dialog-filter, and byte-limit constants.

LightBurn implements a bounded **scan -> strict parse** path on top of that
contract. `scan_lightburn_file()` and `scan_lightburn_project()` inspect bounded
XML structure without invoking the existing `_parse_shape()` geometry vectorizer.
The manifest reports the LightBurn format version, referenced cut layers, layer
names/mode hints/object counts, coordinate-processing facts, review warnings,
known approximations such as ellipse/rounded-rectangle/Bezier flattening and
vector-backup text, and explicit unsupported/blocking content. Embedded bitmaps,
text without a usable vector backup, unsupported shape/path types, malformed XML,
wrong extensions, and size-limit violations are surfaced fail-closed before
strict vector parsing. `load_lightburn_project()` reads the file once, performs
this scan, and enters the unchanged strict parser only when the manifest has no
blocking errors or unsupported features; the strict parser remains authoritative
for geometry conversion and detailed validation. Focused LightBurn scan/import,
manifest, and desktop-import tests reported **43 passed, 1 skipped**; the skip
was the PySide6-dependent desktop widget test in the local environment.

Foreign G-code now has the matching bounded **scan -> strict translate** path.
`scan_gcode_file()` and `scan_gcode_project()` follow modal units, absolute/
relative positioning, feed, power, laser mode, plane selection, and supported
G/M command state without calling `_motion_points()` or `_append_move()` and
therefore without sampling arcs or assembling E3 geometry during discovery.
The manifest reports source line/powered/travel counts, reconstructed feed/
power/M3-M4 operation combinations, coordinate-mode facts, stated or inferred
S-scale review information, arc-sampling approximations, and omitted controller
or work-coordinate behavior. Unsupported axes, words, G/M codes, block-delete
or checksum syntax, invalid/missing feed, conflicting or exceeded S-scale hints,
non-XY arc planes, and other known untranslatable constructs are reported
fail-closed before the existing strict translator runs. `load_gcode_project()`
reads the source once, scans it, and calls the unchanged strict translator only
when the manifest is ready. `ImportScanManifest` now also has immutable
`source_facts` for non-warning source statistics. Focused G-code/LightBurn scan
and importer plus manifest coverage reported **70 passed, 1 skipped**; the skip
was the PySide6-dependent G-code desktop widget test in the local environment.
Ruff, compileall, and diff checks were clean.

SVG now has the matching bounded scan and exact-source strict adapter.
`scan_svg_file()` hashes the exact bounded bytes and delegates to the capped SVG
parser only to produce detached geometry facts; it never constructs a
`SceneObject`. Natural physical dimensions, path/point counts, viewBox and
coordinate mapping, flattening approximations, parser errors, and incomplete
content are represented in the manifest. Existing fail-closed SVG semantics are
preserved: parser warnings become review blockers, and `load_svg_project()`
verifies the approved digest before the authoritative strict parse and native
object conversion.

Raster image discovery now reuses the bounded stable encoded-payload and header
metadata contract. `scan_raster_file()` reports exact encoded-byte SHA-256,
format and pixel dimensions, bit depth/channels/orientation/decode budget, and
the desktop's existing fitted-size and grayscale/dither facts without decoding
pixels or creating a project object. The post-review
`read_raster_asset_payload()` call requires the approved digest before any layer,
active-layer, history, selection, or object mutation. Newly created raster
layers are explicitly output-disabled; encoded-size, dimension, header,
decode-budget, display sizing, and one-/two-command undo behavior otherwise
remain unchanged.

All four formats now use that shared discovery contract in one native
desktop pre-import review flow. After file selection, the desktop runs the
format's bounded file scan and presents a reusable window-modal
`ImportReviewDialog` before invoking the existing strict loader or changing the
active authoring tool. The dialog shows source identity/size/format/capability
information, discovered layers or reconstructed operations, source and
coordinate facts, warnings, approximations, unsupported features, and errors,
including explicit empty states for facts a format does not report. Errors or
unsupported features disable **Import**; valid and warning-only manifests still
require an explicit **Import** action. Rendering is presentation-bounded to the
first 200 layer/operation rows and first 200 entries in each repeated fact or
message section; exact omitted counts remain visible and the immutable manifest
itself is not truncated. Cancel and blocked review return before
project layers, objects, history, selection, active layer, or creation/point-
pick authoring state changes. The existing strict SVG, raster, LightBurn, and
G-code paths remain authoritative and verify that newly read bytes match the
reviewed manifest's SHA-256; a changed source aborts without project/history/
selection/authoring mutation. Each format retains its existing undo/redo
transaction granularity. No project schema, controller path, motion, arming,
execution, or laser behavior changed. Focused Windows Python 3.13 verification
passed **204 tests** across the importer manifest, all four bounded scanners and
strict paths, the reusable offscreen Qt dialog, all four desktop integrations,
and their source/documentation contract. An additional raster-focused toolpath
run passed **32 tests** with 39 deselected.

Coverage includes explicit approval, warning-only acceptance, independent error
and unsupported-feature blockers, Cancel preservation of document/history/
selection/authoring state, deterministic UI truncation with exact omitted counts,
raw-byte digest generation and propagation, same-size source replacement after
approval, scan-before-strict ordering, strict-parser/probe rejection, and each
format's existing undo/redo behavior. Repository-wide Ruff, package compileall,
and diff checks also pass.

This flow is automated-test and offscreen-widget verified only; it required no
physical controller, motion, camera, or laser test.

Machine Setup now has a sixth **Coordinate Audit** tab after Accuracy
validation. Its refresh, JSON report copy, and clicked-point inspector are
read-only; only the explicit **Home / park and capture audit view** action uses
the existing laser-off parked precision-capture path. The audit reports the
detached running saved-machine/profile identity, expected and actually active
calibration profiles, controller/GRBL coordinate state, work and guarded beam/
carriage authorities, camera/lens/bed-map state, accepted support geometry, and
only the explicitly configured `machine.honeycomb_span_mm`. A missing physical
span or expected/active calibration mismatch is an explicit readiness blocker.
Bed Mapping now displays that same saved-machine span read-only and supplies it
unchanged to automatic four-edge detection and the three-hint fallback. An
unset span displays **Not configured** and blocks both detector workflows before
detection and without controller work, directing the operator to Machine
Manager instead of inventing a 190/191 mm value.
Captured evidence retains Home/park result, MPos/WPos/WCO, workspace/G92,
commanded-versus-reported error, before/after stability, timing, and bed-map
identity after normal motor-release cleanup clears current coordinate trust.
`MachineService` obtains these diagnostic samples with only the GRBL realtime
`?` byte through its existing transport, including `e3bridge://`; malformed or
missing frames fail the sample without granting authority or changing
controller state. Sampling refuses a running streamed job under the shared
command lock before transmitting the realtime byte and preserves job,
transport, coordinate, session, reconnect, authorization, and log state. The
audit overlay adds machine/work, guarded output, accepted support, and positive-
axis references; the shared Bed Mapping overlay remains axis-arrow-free by
default. A clicked audit point is bound to the published image, bed-map digest,
and accepted-support state/frame identity. It clears when a new audit capture
starts or any of that evidence changes, and copied reports recheck staleness so
they cannot retain an obsolete point. Follow-up review coverage now proves the
complete Home/park, before-sample, raw-burst, after-sample, motor-release, and
deferred-processing order; rejects capture-evidence publication when the image
write fails; directly exercises the optional Home-position snapshot through
`MachineService`; and verifies malformed realtime samples preserve existing
coordinate, reconnect, session, and authorization state. Permanent-fixture
reach editing and bounds proposals remain absent for the next increment. The
requested five-file focused Windows suite passes 276 tests, and the complete
11-file Increment 2 focused suite passes 434 tests; the precision-capture file
accounts for 19 tests. Repository-wide Ruff, compileall, and diff checks also
pass. This increment is automated-test and offscreen-widget verified only; no
physical controller, motion, camera, or laser test was performed.

The multi-machine foundation now carries an optional, validated physical
honeycomb ruler span on each saved machine. It remains unset for every generic
and Ender-3 S1 Pro starting profile until an operator explicitly configures it
in Machine Manager. `CoreRuntime` also passes a detached snapshot of the stable
running-machine, profile, camera, and calibration identities into `AppContext`.
The legacy Coordinate Audit fixture-reach evidence model is restored as
diagnostic-only state under
`<data_dir>/machine_state/<stable-machine-id>/fixture_reach.json`; renames retain
that path and duplicates begin without evidence. Preserved machine-state
directories permanently reserve their IDs even after registry deletion, so a
new or duplicated machine cannot inherit orphaned physical evidence. Only the
physical `legacy-config` machine can claim the old global `fixture_reach.json`,
using strict, no-clobber copy metadata; profile-created and duplicated machines
cannot win by launching first. The legacy source is never changed, malformed
evidence or claim metadata is ignored fail-safe, and a second machine cannot
inherit the claim. Explicitly saving valid scoped evidence clears a stale
in-process migration error because that scoped evidence is then authoritative.
Focused configuration, registry, runtime-identity, offscreen Machine Manager,
evidence isolation/restart, rename/duplicate, and migration tests pass. That
foundation increment changed no controller, motion, G-code, bounds, arming, or
laser behavior and was not physically tested.

The native desktop now has a bounded foreign G-code design importer for `.gc`,
`.gcode`, `.nc`, and `.tap`. It translates supported 2-D G0/G1/G2/G3 motion into
ordinary E3 path objects and reconstructs Line layers from modal feed/power
combinations. Imported layers are always output-disabled and foreign programs
are never streamed directly; subsequent execution still requires E3 generation,
exact Preview, and the guarded START JOB path. Unsupported coordinate-changing
or non-2-D commands fail import instead of being guessed. Focused parser and
offscreen desktop tests are included; this importer is not physically verified.

The native desktop now requires the final execution sequence **Generate -> exact
Preview -> START JOB** for both project programs and prepared Machine Setup
programs. The Preview is window-modal while it is open, so project authoring and
other main-window controls cannot mutate the reviewed source. Its distinct
bottom-right **START JOB** control closes the Preview and synchronously delegates
to the unchanged guarded `run_current_job()` path, leaving software STOP
accessible while preserving every stale-revision, support/map binding, raster
identity, connection, Home, motion, arming, bounds, and streamed-program check.
The main Job panel and Laser Tools menu can only open Preview; they no longer
offer a direct execution bypass. **Prepare Start Here…** still replaces rather
than executes a program, and the replacement must complete its own exact
Preview. Focused offscreen Qt acceptance and rejection tests verify modality,
blocked parent interaction, guarded handoff, Preview dismissal, STOP access,
stale rejection, bypass removal, Start Here non-execution, deferred rendering,
and unfinished-close invalidation. The mandatory exact-Preview execution gate
and its **START JOB** handoff were physically verified on 2026-08-16 through the
real Windows-laptop -> Raspberry-Pi -> controller/laser path described below.
Focused verification passed 333 tests:
15 exact-Preview tests, 57 desktop job tests, 49 compact-panel/layout/template/
runbook tests, 44 Machine Setup tests, and 168 core machine safety tests.

Earlier on 2026-08-16, a real powered job completed its streamed cutting
G-code, but automatic post-job Home / park did not finish and E3 remained in
the running state. A subsequent manual Home / park request was correctly
rejected with
`Cannot move to the photography position while a job is running` because the
completion worker still owned the machine. Ordinary Home / park already handled
this controller's missing terminal `$H` acknowledgement by requiring realtime
evidence of active `Home`, `Homing`, or the observed `Run` state followed by
`Idle`; automatic completion was still using the generic acknowledgement-only
running-job command path. Both paths now share the same fail-closed GRBL homing
acceptance state machine while retaining their distinct command-lock and
running-job/STOP ownership. Focused tests verify normal `ok`, each accepted
active-to-Idle transition, idle-only and malformed evidence, alarm/error,
disconnect, timeout, STOP cancellation, park/release ordering, and terminal job
publication. After that correction, the operator completed a physical acceptance
run on the same Windows-laptop -> Raspberry-Pi -> real-controller/laser rig:
**Generate -> modal exact Preview -> START JOB -> powered cut -> automatic Home
-> configured photography-position park -> motor release -> E3 Complete**. The
previous stuck `job.running=True` result did not recur. This physically verifies
the mandatory exact-Preview execution gate, the post-job GRBL homing correction,
and successful powered-job progression through Home, park, motor release, and
terminal Complete on this rig. It is not a safety certification and does not
physically verify STOP, alarm, error, timeout, disconnect, or other failure
paths; those remain automated-test evidence unless separately recorded.

Physical Pi 3 B+/C920 validation showed that OpenCV raw mode is not viable:
V4L2 negotiated MJPG at 1920×1080/30 fps, but `CAP_PROP_FORMAT=-1` was rejected
and `VideoCapture.read()` returned decoded 6,220,800-byte BGR frames. The
OpenCV raw-mode probe has therefore been removed. `CameraService` now first
attempts a narrow Linux V4L2 MMAP backend for persistent device paths. It
negotiates MJPG and the configured dimensions/rate, retains each bounded JPEG
packet unchanged, and decodes that same packet once for all ordinary camera and
precision consumers. Both representations share sequence, generation, and
capture timestamp. Native-size monitor requests forward the exact packet with
no resize or encode; unavailable native capture closes fully before the normal
decoded OpenCV fallback opens. That fallback is 1280×720/10 fps/quality 78,
while direct mode may deliver 1920×1080/10 fps. The V4L2 ABI abstraction,
buffer lifecycle, exact-byte path, fallback, camera lifecycle, precision, and
monitor behavior are automated-test verified without hardware. Physical native
V4L2 operation on the Pi 3 B+/C920 measured approximately 28.6% total CPU busy
(70.5% idle), roughly 116 MB RSS, and about 47.11 Mbps TX while the Raw Live
Monitor reported `DIRECT MJPEG` at 1920×1080 with a 10 fps monitor target. The
earlier approximately 1088 fps camera status was an accounting defect: the
sample was taken after V4L2 dequeue but before source-JPEG validation and decode,
so it did not represent a physical camera rate. Publication FPS now includes
validation and decode time; that correction is automated-test verified but has
not yet been physically rechecked. The physically measured prior transcoded
720p/10 fps baseline was about 18.11 Mbps TX, 2.2–2.4 CPU cores of active work,
and 146 MB RSS; each result is one observed configuration, not a universal
expectation.

The Raspberry Pi camera bridge now offers a bounded authenticated raw-monitor
mode on its existing `e3camera://` socket. One persistent connection carries
JPEG frames from the sole Pi-owned `CameraService`; the server and desktop both
use latest-frame replacement semantics, with two monitor clients maximum and a
4 MiB per-frame ceiling. The desktop now prefers 1920×1080 at 10 fps, offers
5/10/15 fps, and separately reports Pi-side usable-frame **Capture** FPS,
desktop socket **Network** receive FPS, Qt **Display** FPS after latest-frame
replacement, and source-frame **Age**, alongside the direct/transcoded mode.
It remains independent of machine connection and calibration authority. Focused
loopback, camera lifecycle, transport-timestamp, receive-accounting, and
offscreen desktop tests pass. The performance observation above is physical;
the corrected Capture / Network / Display / Age values are automated-test
verified only. Direct-path latency, controller responsiveness, precision-capture
coexistence, and go2rtc service impact remain physically unmeasured and must not
be inferred from desktop CI.

On 2026-08-16, physical Windows-laptop monitoring at 1920×1080 `DIRECT MJPEG`
measured Capture 17.4 fps, Network 10.1 fps, Display 7.7 fps, and source Age
7 ms with a 10 fps target. With a 15 fps target it measured Capture 17.2 fps,
Network 14.9 fps, Display 8.3 fps, and source Age 29 ms. The nearly proportional
increase in Network delivery without a corresponding Display increase localized
an approximately 8 fps ceiling to the Windows desktop presentation path while
Pi capture and socket delivery remained faster. The Raw Live Monitor now takes
the same bounded encoded JPEG packets through a narrow remote-camera API, keeps
only the latest received packet while a separate presentation worker is busy,
and uses Qt JPEG decoder-assisted scaling toward a thread-safe snapshot of the
current display size. The GUI thread now only constructs the `QPixmap`, presents
it, and updates diagnostics; ordinary decoded `monitor_frames()` callers remain
compatible. A repeatable local synthetic 1920×1080-to-899×506 comparison reduced
measured GUI-thread conversion/presentation work from 4.07 ms to 0.15 ms per
frame and total decode/preparation plus presentation from 8.49 ms to 4.82 ms;
the new 4.66 ms preparation occurs off the GUI thread and avoids full-resolution
BGR and QImage intermediates in this monitor path. The architecture, bounds,
dimensions, two-stage latest-frame replacement, resize behavior, diagnostics,
and lifecycle are automated-test and local-benchmark verified. The improvement
is **PHYSICALLY UNVERIFIED** until the real Windows-laptop 15 fps monitor test is
repeated; no 15 fps Display-performance conclusion may yet be inferred.

Explicit controller replacement now performs disconnect/laser-off cleanup
under the UI action's original STOP generation, then captures and binds the
post-cleanup generation for the replacement connection. The single generation
advance caused by disconnect is required; an additional concurrent STOP before
connect or a STOP during connect still cancels replacement. Both the native
Machine panel and Machine Setup use this shared service operation. Successful
replacement remains HOME REQUIRED with coordinate and jog references invalid;
there is no automatic Home, motion, job resume, arming, or laser authority.
This correction is automated-test verified and awaits physical STOP/reconnect
validation.

The desktop now presents an explicit **Reconnect** action when an established
controller session is marked RECONNECT REQUIRED. This operator action performs
one disconnect followed by the ordinary connection path; it never retries in
the background, homes, moves, resumes, or arms. A successful replacement
session remains HOME REQUIRED with coordinate and jog references invalid. A
failed replacement remains safely disconnected. Native modal message boxes
also receive one queued polish/update/repaint immediately after their first
show event. This application-wide, non-blocking workaround addresses the
observed Linux compositor/backing-store first-exposure failure without changing
modality or the dark theme; focused offscreen Qt coverage verifies visible
content, queued repaint execution, modal results, and parent usability. Both UI
corrections are automated-test verified and await physical Linux validation.

CI now validates the desktop on Windows only. Fast Development CI runs for
development pushes under `fix/**`, `feature/**`, `agent/**`, and `cleanup/**`;
it uses Windows Python 3.12 for Ruff, dependency/bytecode validation, and the
complete desktop-enabled pytest suite with four bounded xdist workers. Branches
outside those patterns, including `architecture/**`, receive the full automated
gate when opened as a pull request to `main`. The main compatibility workflow
runs Windows Python 3.10 without desktop extras, Windows Python 3.12 with desktop
extras, and a separate Windows Python 3.12 Ruff job for pushes/PRs targeting
`main` or `desktop-v1` and for manual dispatch. Linux desktop CI is no longer a
supported compatibility gate. Existing Linux/Pi runtime components and any
legacy Linux packaging path are separate concerns and require focused
verification when changed.

Physical reconnect after the software STOP test found the controller alive but
alarm-locked: settings queries succeeded while connection normalization's `M5`
was rejected with GRBL `error:9`. Connect now shares Home / park's narrow
pre-home recovery: only this exact consumed rejection, and only with mandatory
Home / park configured, permits `$X` followed by a second acknowledged `M5`.
The connection remains HOME REQUIRED with no coordinate or jog reference, and
Connect never homes or moves automatically. Other errors, alarms, unlock or
second-`M5` failures, disconnects, and timeouts fail closed. This correction is
automated-test verified but awaits physical reconnect and Home / park
validation on the controller.

Physical jog validation found that the requested feed was emitted on `G0`, so
GRBL used its configured rapid positioning rate and the Jog panel's speed field
did not control the observed motion. Guarded jogging now retains its trusted
absolute-target architecture but emits laser-off feed-controlled `G1` motion;
the requested feed and existing travel-feed ceiling therefore apply to the
controller move. This correction is automated-test verified but awaits a
repeat physical jog at both low and normal feed settings.

On 2026-08-15 the Raspberry Pi hardware-node candidate was physically exercised
with the installed controller and camera. The remote camera delivered the
configured 1920 x 1080 MJPG stream at 30 fps, and the authenticated controller
bridge connected and delivered commands to the physical GRBL-derived
controller. A `$H` command completed the physical double-touch homing motion,
but no terminal `ok` reached E3, so the prior acknowledgement-only Home / park
path timed out and correctly refused to issue the park move. The application
now prefers the normal `$H` acknowledgement but can verify this controller from
a realtime active-homing (`Home`/`Run`) to `Idle` transition; an immediately
idle, alarmed, disconnected, stopped, or otherwise ambiguous exchange fails
closed and requires reconnect. The bridge source and loopback coverage show
that complete serial lines, including `ok`, are forwarded without filtering,
but the physical session did not capture Pi-side raw serial traffic. Therefore
the available evidence cannot distinguish a controller-omitted acknowledgement
from a lower-level serial/bridge loss. The later 2026-08-16 powered-job
acceptance run described above physically verified the corrected shared homing
exchange and configured park pose in that successful completion path.

Initial transport opening now receives one bounded retry only before this
`MachineService` instance has established any trusted controller session.
Configuration, protocol, and bridge-authentication errors are not retried, and
an established session that becomes uncertain is never automatically
reconnected or resumed.

This historical entry describes the superseded `E3BRIDGE/1` raw-serial
candidate, not the current `E3MACHINE/2` ownership semantics. That earlier
Windows-to-Raspberry-Pi candidate placed authenticated controller and camera
transports underneath the existing guarded services. Its controller path kept
the desktop `MachineService` as the command owner and used a Pi-local realtime
stop/reset plus `M5` after client loss. The camera path
keeps V4L2 ownership and precision acquisition on the Pi, transfers retained
frames to the desktop, preserves sequence/generation/control diagnostics, and
rejects mismatched Pi/desktop capture profiles before startup. Twelve focused
loopback tests and Python bytecode compilation pass in an isolated harness. The
restored GitHub Actions matrix passes on Ubuntu with Python 3.10, 3.11, and
3.12, and on Windows with Python 3.10 and 3.12; the Python 3.12 jobs include the
desktop extras and offscreen Qt suite. Ruff, dependency checks, and bytecode
compilation pass in that same run. Local Linux verification also includes 1,758
repository-wide tests, 1,371 portable tests without PySide6, and the focused
network tests. The camera mode, bridge authentication, and controller command
delivery are now physically bring-up verified as described above. Network-loss
cleanup and calibration repeatability remain physically unverified. Corrected
post-job homing, photography parking, motor release, and powered completion were
physically exercised in the specific successful 2026-08-16 acceptance run; the
unexercised failure paths retain automated-test evidence only.

Fine-registration reset now immediately re-evaluates the retained eight-mark
capture against the restored base map instead of discarding the review and
leaving **Apply reviewed full-bed map** disabled. The full-map axis-span gate
also allows the support-contained pattern's measured 69.9% Y span (68% minimum,
with the independent 35% hull-coverage gate retained). The saved 2026-08-14
capture then qualifies with seven inliers, 0.084 mm RMS, 36.7% hull coverage,
and 0.695 mm maximum modeled correction. Focused reset and homography-gate tests
pass; applying this refinement has not yet been physically tested.

After applying that full-bed map, the first automatic honeycomb re-teach fell
back to unseeded segmentation because the accepted teaching metadata correctly
carried the superseded bed-map digest. It selected the outer right ruler edge
and failed the nominal-size gate by 14.38 mm. Setup re-teaching now permits the
integrity-checked prior teaching image to seed pixel registration even when its
map digest is stale; four fresh edges are still independently fitted and gated
through the new map before acceptance. The exact 2026-08-14 capture now resolves
the cutting surface with mapped side disagreement below the 2.85 mm teaching
limit. Execution continues to reject stale map/support bindings. Focused stale-
map and legacy-upgrade tests pass; the corrected button flow awaits live retry.

The powered dense 5×5 fit now places its five machine-axis nodes across an
exact 180 × 180 mm center span on the current support, retaining 5 mm crosses.
Every cross endpoint must remain inside both the freshly taught support and the
explicit configured honeycomb-output polygon. That exact polygon is stored with
the calibration session, used for generation and MachineService preflight, and
passed unchanged at Start. Profiles without an explicit polygon retain the
legacy conservative support/machine-rectangle layout. Focused 180 mm generation,
session-binding, and desktop calibration-job tests pass; the expanded pattern
has not yet been physically run.

New projects now contain the operator-supplied E3 10 W starting profiles in
Cuts / Layers slots 00–12: seven line-cut profiles (paper, two plywood types,
MDF, opaque black acrylic, vegetable-tanned leather, and cardboard/chipboard)
followed by six raster profiles. Speed, power, passes, raster interval, scan
angle, overscan, semantic colors, and zero power-correction values match the
2026-08-14 workbook except for the operator's corrected slot-00 paper cut:
1500 mm/min at 100% power. Existing saved projects are not rewritten. These are
unverified starting values for a machine with no air assist, not guaranteed
material settings; no profile has been physically acceptance-tested here.

Machine Setup jobs remain absolute machine-coordinate programs, but when the
active authoring canvas is honeycomb-local their workspace and popup previews
now use the current rigid support transform. This corrects a display-only bug
that drew valid fine-registration targets outside the visible support by
treating machine coordinates as local coordinates. Generated G-code and
machine preflight are unchanged.

The desktop now models an automatically detected honeycomb as a real movable
job coordinate system instead of conflating it with the persisted machine
rectangle. New projects created with a current, execution-verifiable schema-2
support use explicit `honeycomb_local` coordinates from X0/Y0 to the physical
span configured for the running saved machine; legacy schema-1 projects migrate
explicitly as `machine`. The four independently fitted and mapped corners are
reduced to a closest-fit right-handed rigid frame so small edge disagreement
cannot shear project geometry. Camera rectification, the authoring grid, Trace,
and toolpath preview can share that local frame.

Vector, fill, image-raster, and frame output is planned in local coordinates,
then rigidly placed in machine coordinates before laser-spot correction.
Generation independently checks local support bounds, placed beam geometry,
spot-corrected controller paths, and the selected execution authority. A prepared
honeycomb-local job binds the support pose, a digest of the complete bed map,
and the exact configured output polygon reviewed with the job; Preview, export,
Start Here, and Start reject stale bindings. Every powered segment in a
post-map Machine Setup pattern is likewise contained in the measured support,
the complete program remains machine-bounded, and the session is bound to that
exact support/map identity. Start first runs static program and machine-bound
preflight, rechecks the immutable support/map/output-polygon binding without a
camera capture, performs one laser-off Home, and begins the validated program
without parking at the photography pose. A missing, legacy, corrupt, or stale
support binding fails closed. The
powered base-map pattern is the intentional bootstrap exception because that
map is required to express a support in machine coordinates; it remains bounded
by the configured machine area and requires a restrained sacrificial sheet over
the exact reviewed pattern.
The complete 190 mm surface is available for authoring. On 2026-08-13 the
operator explicitly confirmed that the physical output authority covers a
210 Ã— 210 mm square centered on the detected support. The local hardware
configuration records that exact fixed machine-coordinate polygon, in support
order, as `(18.218005, 29.679375)`, `(228.217364, 30.198421)`,
`(227.698319, 240.197779)`, and `(17.698960, 239.678734)` mm. Its
machine-axis-aligned bounds are X17.698960..228.217364 and
Y29.679375..240.197779 mm. It is not inferred from later camera detections and
does not alter camera calibration or Home/park bounds. Unreachable geometry is
rejected.
Core coordinate, rectification, persistence, toolpath, Trace, and offscreen
desktop tests pass. This workflow has not yet been physically acceptance-tested,
and physical stops/controller max-travel values remain operator verification
items. An automatic schema-2 teaching reference was accepted on 2026-08-13,
but it has not yet passed a repeated fresh-capture or powered-job physical
acceptance test; do not treat its presence as proof of output accuracy.

The desktop live camera overlay and Trace review now rectify directly into a
current honeycomb's X0..width, Y0..height frame. Without a current frame they
retain the machine-coordinate camera-area fallback, which can expand beyond the
configured rectangle solely to avoid cropping visible evidence. The active
saved schema-2 support spans about machine X27.0..218.7 and Y38.7..231.1 and is
execution-verifiable in software, but is not physically verified.
A separate green polygon maps the explicit guarded-output square into
honeycomb coordinates. Its local bounds are Xâˆ’10..200, Yâˆ’10..200: exactly
10 mm beyond each edge of the 190 mm support. Trace, template review, project
generation, and `MachineService` preflight use the same polygon. Only a job
bound to the current honeycomb signature may opt into it; ordinary jobs retain
the legacy rectangular policy, and the immutable preflight result binds the
exact polygon so a config change invalidates it. Focused rectification, display,
detection-boundary, color-sampling, live-overlay routing, cache-isolation, and
authority-separation tests pass. Direct honeycomb-local live-overlay framing was
interactively confirmed on 2026-08-13. A follow-on Trace attempt exposed a stale
in-memory 192 × 192 mm empty project after accepting the 190 × 190 mm
support: camera framing had updated, but the project-frame callback ignored an
already-local document. Clean, empty, unsaved projects now reconcile
local-to-local dimensions when the support changes and again immediately before
Trace or color sampling. Saved, dirty, and nonempty projects retain the strict
mismatch rejection. Focused offscreen lifecycle tests pass; the corrected Trace
button flow has not yet been repeated on hardware.

On 2026-08-12, a live powered fine-registration run exposed that its historical
fixed work-area fractions were independent of the newly detected honeycomb.
The saved support began near machine Y37.3 mm while the generated cross extended
to Y32.5 mm, and the operator reported a mark beyond the honeycomb. Fine
registration now derives its targets in the current detected support frame,
clips them to the guarded machine rectangle, verifies the complete cross extents
inside both regions during generation, binds the prepared session to that exact
support reference, and repeats polygon containment on the exact powered G-code
immediately before job preflight. The same support/map binding, containment,
and Start-time revalidation now applies to accuracy validation, dense 5×5 fit,
4×4 mesh validation, and shifted confirmation jobs. Dense target rectangles
remain machine-axis aligned inside the shrunken support-contained region so the
Cartesian mesh remains valid. Focused generation/start acceptance and rejection
tests pass; this correction has not yet been physically run.

Base-grid detection now rejects duplicate, irregularly spaced, and
edge-contaminated 25-point OpenCV lattices and continues through its remaining
candidate thresholds. The saved 2026-08-12 C920 capture that previously chose
a duplicate lattice containing a false bottom-edge hardware point now resolves
the correct keyed 25-point grid. Honeycomb-ruler periodicity now refines the
integer autocorrelation peak to a fractional-pixel tick pitch, avoiding the
observed 5 px quantization that reported 206.8 ticks across a physical 190 mm
ruler. Focused synthetic detector tests pass; the honeycomb change has not yet
been repeated with a fresh physical three-hint detection.

Honeycomb hints no longer act as ruler endpoints. They select the X ruler,
approximate shared zero/intersection, and Y ruler.
Vision fits both baselines, measures fractional-pixel 1 mm pitch, uses their
intersection as the detected zero, and projects the configured physical span;
the active bed map still independently checks that projected span before the
optional reference can be saved. Focused unit, application, and offscreen UI
tests pass; this redesigned interaction awaits a live C920 retry.

Honeycomb reference detection is now automatic-first. Edge-density segmentation
locates one dominant rectangular honeycomb, fits all four cutting-surface edges,
and retains their four mapped intersections as measured evidence. The active
bed map orders those raw corners as origin, +X, opposite, and +Y; the configured
physical span defines the nominal honeycomb-local dimensions but does not
fabricate the observed edge lengths or corners. Printed tick recognition is not
required. Accepting the reviewed result creates an execution-verifiable schema-2
reference. The three-click path is explicitly a last-resort display/diagnostic
fallback for failed or ambiguous automatic results and cannot authorize
honeycomb-local or powered post-map execution. Synthetic segmentation and
focused application/UI tests pass; the full automatic pipeline awaits a live
C920 capture.

Accepting automatic detection stores the exact reviewed homed-bed teaching PNG
and metadata as atomic files bound by its four image corners, image digest,
bed-map digest, and support-frame digest. Automatic re-detection can register
that image from support-local matches with bounded count and spatial coverage.
Job Start deliberately does not recapture or register it after the operator has
traced and reviewed the current image.
The older annotated `1920x1080-manual-focus-010` template and schema-1 support
artifacts remain diagnostic only and cannot pass the execution predicate.
Synthetic support-local registration, moved-support, scale-mismatch, and
coverage-rejection tests pass. A 2026-08-13 live Start capture exposed that the
former automatic pose check both falsely rejected interrupted ruler edges and
caused a redundant Home/park/capture/release/Home sequence. At the operator's
direction, Start now uses one Home with no intervening camera pose check. This
correction has not yet completed a powered physical job.

Native Machine Setup camera views now support a validated clockwise quarter-turn
presentation transform. The local C920 hardware profile is set to 90 degrees so
its sideways mount displays machine X toward screen-right and machine Y toward
screen-top. Overlays are rotated as image pixels and pointer selections are
inverted back to the original sensor coordinates; lens images, bed points,
homographies, saved captures, controller axes, and output bounds remain
unchanged. Configuration and offscreen picker-coordinate tests pass. The rotated
view has not yet been interactively inspected in the live hardware process.

The cutting-template designer now authors twelve semantic shapes through one
Qt-independent geometry vocabulary: rectangle, rounded rectangle, circle,
ellipse, capsule, triangle, diamond, regular polygon, star, one-flat circle,
two-flat circle, and washer. New `shape_grid` recipes retain row-major identity
and bounding-box spacing; legacy `rectangle_grid` version-1 templates migrate
as rounded rectangles. Washer OD/ID is stored as one logical compound object,
and containment-aware vector ordering cuts nested closed contours deepest-first
before nearest-travel optimization. Every nested contour now completes all of
its configured passes before its containing parent begins, independent of
winding, while unrelated contours retain pass-major ordering. Template features
carry semantic shape and
optional hole ratio, while legacy/unknown matching retains geometric fallback.
Direct Trace classification now recognizes high-confidence circle, ellipse,
triangle, regular-polygon, and washer silhouettes without forcing weak
candidates. Washer recognition uses parent/child contour hierarchy only from
filled-region masks and requires independent circular residual, circularity,
diameter-ratio, and strict concentricity gates; its proposed vector retains
both contours as one semantic object.
The Trace **Geometry output** selector now names this mode **Best-fit analytic
shapes** rather than the obsolete **Fitted rounded rectangles**, and recognized
washer rows display their fitted outer and inner diameters explicitly.
Repeated-grid review now flags direct cells whose observed rotation/dimensions
or repaired center disagree materially with the shared family as **damaged?**.
It also compares shrunken-interior intensity variation and edge density across
the current grid, marks strong exposed-bed outliers **likely cut/open**, and
leaves both categories unchecked while retaining their proposed traces. This
texture fallback remains conservative. When the current schema-2 support has
an integrity-checked accepted empty-honeycomb photograph, Trace additionally
rectifies that image through the same support-local frame and marks cells whose
interiors strongly match the exposed honeycomb. Image hash, complete bed-map,
and support-frame digests must all match; stale evidence is ignored. Synthetic,
application, and controller wiring tests cover both paths.

The saved 2026-08-14 07:37 C920 recovery frame was replayed read-only through
the current post-mesh map. Trace finds 14 direct rounded rectangles as a coherent
2 x 7 grid at 100% confidence. The accepted honeycomb background leaves the four
already-open cells unchecked and selects the ten remaining labels. At zero
border offset, its shared 83.24 x 22.45 mm fit differs from the intact printed
borders by about 0.02 mm in width and 0.04 mm in height. The operator's visible
`-0.20 mm` uniform offset instead trims every proposed edge by 0.20 mm, so it is
not an alignment-preserving cleanup setting. No powered recovery cut was run as
part of this software verification.
Automated geometry, migration, UI, matching, Trace, and toolpath tests cover
the implementation, including three-pass washers, reversed contours, multiple
washers, imported compound paths, and three nesting depths. No generated shape
or containment ordering has been run on physical hardware.

Per-operation and material-recipe Power Correction is implemented as a bounded,
material-specific commanded-power bias layered over GRBL `M4`. Vector paths use
turn-angle severity and the configured acceleration model to add at most three
collinear ramp blocks on each side of a real junction. Raster correction first
credits laser-off overscan and changes image-area power only when that overscan
is shorter than the modeled braking distance. Zero correction retains the prior
program shape; raw G-code keeps `M4`, laser-off rapid travel, guarded inline `S`
on `G1`, and final `M5`. Projects and migrated material databases default both
new values to zero. Focused model, mapping, geometry, raster, exact-Preview, UI,
material, and guarded-stream tests pass. No corrected powered job has been run
on hardware. The platform-neutral suite passes 1,596 tests with the 103
loopback-server security tests run separately outside the socket-restricted
sandbox; all 1,699 tests pass. Repository-wide Ruff checks also pass.

On 2026-08-14, toggling an object's **Visible** checkbox exposed a native Qt
re-entrancy crash: the synchronous history listener rebuilt the object tree
while its `itemChanged` callback still owned the emitting row. Object edits now
use a queued connection so the native callback returns before the undoable
document refresh. The focused object-list, history, and workspace suites pass
64 offscreen tests; this fix has not yet been interactively retried.

On 2026-08-11, a read-only `$$` query against the connected controller reported
`$120=500.000` and `$121=500.000` mm/s², matching the configured
`laser.preview_acceleration_mm_s2=500`. It also reported `$110/$111=10000`
mm/min, `$30=1000`, and `$32=1`. Controller/firmware identity and physical
acceleration were not independently measured, so this is stored-setting
readback rather than physical verification.

Exact Job Preview operation rows now translate generated powered-motion feed
into mm/s and percentage of the configured work-feed limit, independently from
the requested percentage/controller `S` power pair. The live move readout uses
the configured travel limit for rapid moves and work limit otherwise. Values
come from the immutable parsed G-code plan; this is a review display change and
does not modify generated or submitted controller output, whose required `F`
words remain intact.

All editable desktop dimensional spin boxes now accept explicit metric or
imperial values while continuing to return canonical millimetres to project,
calibration, bounds, and machine code. This includes lengths, areas, feed
rates, template/grid geometry, Trace settings, material/layer settings,
Machine Setup values, and editable jog distance. The browser's coordinate,
size, calibration-point, feed-rate, and coordinate-CSV inputs provide the same
conversion. Focused unit/parser and affected desktop widget suites pass; no
controller, calibration, or G-code storage schema was changed.

Repeated-grid Trace gap suggestions now optionally inspect only the expected
cell ROI for grayscale boundary evidence after the direct cells establish a
shared rounded-rectangle geometry. Evidence can make a bounded center
refinement without changing the shared dimensions or angle; unsupported gaps
remain explicitly inferred/review-required. Synthetic Trace tests cover
lower-side/side-edge recovery beneath simulated glare, no-evidence fallback,
internal-text resistance, and disabled gap inference. Real C920 glare recovery
on the 2 × 4 label scene remains to be physically validated.

Powered Machine Setup calibration jobs now hand off automatically to their
matching Home / park precision capture and scoring operation after successful
completion. The handoff is bound to the exact prepared filename and is cleared
when a job fails, stops, or is replaced; ordinary project jobs are unchanged.
Preview's **START JOB** does not show a powered-job warning or typed arming
phrase; the guarded run path creates the exact program's one-use temporary
authorization internally and submits the prepared job immediately.

Trace review uses fixed-screen-size, high-contrast numbered badges so object
IDs remain readable over detailed camera imagery. A tri-state **Select /
deselect all** checkbox reflects none, mixed, and complete selection and can
change the whole detection list in one action. The focused desktop panel and
workspace widget suites pass 45 tests for the current implementation. Loose
identical-cell grids also repair only the affected center axis when a missed
edge makes one observed cell materially narrower or shorter than the repeated
size; unaffected placement and rotation remain independently observed.

Historical desktop-foundation branch marker: **`desktop-v1`**

The Linux-machine work through **`15c2c7a`** was preserved and pushed before
the precision-camera feature commit **`99450df`** was integrated by cherry-pick.
The current branch also includes the later calibration, trace, persistence,
transport, and release-hardening work described below; use Git history rather
than this status document as the authoritative source revision.

Baseline before consolidation: **`778532b` — Polish desktop controls and add camera focus workflow**

Consolidated feature commits:

- **`ccac7c2` — Add multi-object camera tracing**
- **`e091e82` — Add batch object insertion command**
- **`c54b143` — Add desktop camera trace workflow**
- **`421091a` — Add offline trace inspection tool**
- **`f42511a` — Exclude local trace artifacts from releases**

Release metadata: **`0.2.0.dev0` (alpha-stage development build)**

This document describes the branch, not only the last release. Update it
when verification, platform support, known gaps, or active feature work changes.

## Repository status at this snapshot

The integrated feature work adds precision multi-frame camera capture
for clarity-sensitive analysis. Fine registration and accuracy validation now
wait for settling, discard buffered frames, require unique fresh frames,
aggregate subpixel cross centers with median/MAD rejection, and reject excessive
temporal jitter. Camera controls are reapplied and V4L2 values are read back
where available. Persisted reports expose frame sequences, sharpness, control
status, inlier/outlier counts, and per-mark jitter. Machine Setup offers a
guarded no-home recapture after the camera pose has been established, allowing
camera/detection variation to be distinguished from homing/pose variation.
Trace, matching, workspace capture, and calibration stills use a stable sharp
frame; continuous preview and streaming remain immediate single-frame paths.

Camera acquisition now has an explicit single-owner contract. Precision bursts,
V4L2 control changes, restarts, and synthetic scene changes cannot overlap;
ordinary preview snapshots remain available as copies of immutable published
frames. Shutdown invalidates the active generation, releases a blocked backend
before joining its reader, and prevents late control or capture results from
being published. The precision timeout now covers control reapplication,
settling, discarded frames, and samples after ownership is acquired. Diagnostics
report negotiated/observed FPS and sequence gaps, and manual-focus readback plus
its fresh post-settle scoring frame are one owned operation. Parked precision
and Trace captures now end the temporary motor hold immediately after the final
raw frame; lens correction and clarity scoring run afterward. A local 45-frame
1080p benchmark measured roughly 0.73 seconds for clarity scoring that no longer
extends the hold. Forty-five focused
camera/control/precision/application tests cover concurrent bursts, preview
contention, restart and shutdown cancellation, blocked reads, bounded V4L2
timeouts, frame-copy ownership, acquisition-deadline semantics, deferred
post-hold scoring, and restart rejection during deferred analysis. Real C920
backend release timing, negotiated-mode reporting, sustained delivery, and
control settling remain physically unverified.

The Camera panel also provides a non-destructive **Test focus range…** operation.
It serially applies each manual value, records three fresh post-settle sharpness
scores, ranks their medians, and restores the original focus/autofocus controls
without saving configuration or invalidating calibration. This permits focus
comparison against an unchanged scene and calibration. Applying or saving a new
final focus remains an optical change and still requires lens and bed
recalibration before precision placement. The sweep lifecycle and result UI are
automated-test verified; physical C920 sweep repeatability remains unverified.

Calibration state is stored in optical profiles keyed by configured camera
resolution and locked manual-focus value. Saving a new focus selects that
profile on the next restart; its lens captures/model, bed map, registration and
validation sessions, and honeycomb reference remain separate. Returning to a
previously calibrated focus restores that stack. Existing unprofiled data is
copied once into the profile inferred from recorded bed-map camera provenance,
with the legacy source retained. Profile selection does not detect a camera
remount or work-plane-height change; those still require fresh calibration.

Machine Setup now has a dedicated fresh keyed 5×5 base-bed mapping workflow for
a remounted camera. It generates 23 regular crosses plus two larger interior
orientation keys through the ordinary zero-power Preview and guarded powered-job path;
no old homography or manual image/machine point entry is required. Unseeded blob
and symmetric-grid detection uses the two keys to resolve all eight grid
rotations/reflections, rejects incomplete, unkeyed, ambiguous, duplicate,
zero-power, stale, or altered sessions, and requires all 25 RANSAC inliers within `0.50 mm`
RMS and `0.80 mm` maximum fit error. Candidate review does not mutate the active
map. Accepted points and the homography are persisted transactionally with
rollback, clear corrections tied to the old map, and record the unambiguous
generated controller-coordinate labels as normal on both axes. Synthetic
rotations/reflections, generation safety, session rejection,
hold/Home/park/release ordering, and transaction rollback are automated-test
covered.

On 2026-08-09 the fresh base workflow was physically exercised with the C920 at
`1920 x 1080`, the configured GRBL serial profile, X/Y work bounds `10..210 mm`,
a 5 mm laser boundary margin, centers `40,75,110,145,180 mm`, `S400`/40% marking
power, and 1200 mm/min. The saved capture detected 25/25 keyed marks with 25/25
RANSAC inliers, `0.1136 mm` RMS fit error, and `0.2685 mm` maximum error; the
operator reviewed the numbered overlay and applied that base map. The already
running pre-change process stored null axis-label metadata, but the keyed map
itself is applied and does not need to be repeated. Future fresh keyed installs
record normal label metadata directly. Exact controller/firmware identity was
not recorded, so this is physical workflow evidence rather than verification of
a named hardware/firmware configuration or a safety claim.

Later on 2026-08-09, guarded laser-off jogging was physically exercised after
Home / park. The operator designated controller coordinate **X245 mm** as the
mechanical X maximum after successfully reaching it without contacting the hard
stop; the carriage had already moved beyond the complete 190 mm honeycomb
support. Direction was then clarified against the configured Home / park pose
`X15 Y195`: **X−10/X+230 mm** established the selected X5..245 envelope, and
**Y−190/Y+20 mm** established the selected Y5..215 envelope. Y−200 reached the
negative endstop near controller Y−5, while Y−190 retained the operator's 10 mm
clearance. The earlier Y−90/Y105 report was a transcription error and is
superseded. The fixed polygon now explicitly requested by the operator reaches
Y240.197779, beyond that earlier laser-off Y215 jog evidence. The operator's
2026-08-13 assertion authorizes the configured software polygon for this
installation, but neither controller travel nor physical beam reach at those
extremes has been acceptance-tested. These are measured mechanical-jog reaches
only. They are not the
camera/calibration rectangle or authorization for laser output. The local
machine work area remains the configured and previously calibrated X10..210,
Y10..210 rectangle; the operator has since clarified that it was never intended
to define the physical honeycomb. It therefore remains conservative execution
authority pending documented laser-spot reach verification, not evidence of the
support size;
with the 5 mm laser boundary margin and zero spot offset, ordinary machine-frame
jobs retain guarded output X15..205, Y15..205. Honeycomb-bound jobs instead use
the separately configured fixed four-corner output polygon. Jogging intentionally
permits travel beyond the camera/calibration rectangle so the mechanical envelope
can be measured without changing calibration provenance. Honeycomb-local display
alone does not grant motion authority.

One packaged, versioned Permanent Camera Setup Guide is now the canonical
operator sequence. It is available from the Machine Setup footer and the main
Help menu, opens modelessly at the current numbered tab, and explicitly covers
the exact-Preview **START JOB** handoff. It warns that timeline Play and
**⏮ Start** only animate and that main **Generate** replaces a prepared
calibration job.
Automated UI/content/package tests keep its five tab headings and exact action
labels synchronized with the application.

Step 3 records a vision-detected pose for the movable honeycomb after a
ruler-overlay capture. Automatic square detection fits four independent edges;
reviewing and saving it records the raw mapped corners, their semantic topology,
and the exact accepted teaching image as an execution-verifiable schema-2
reference. This is separate from camera calibration but establishes the rigid
local job frame. Trace rectifies directly into it while a green polygon shows
guarded machine output.
Three rough clicks remain a last-resort corridor hint. Their fitted
baselines/shared zero can be saved as a legacy visual/diagnostic reference, but
they do not contain four-corner evidence and cannot authorize a honeycomb-local
or powered post-map job.
While those three hints are being placed, the picker shows the clean captured
frame rather than the diagnostic coordinate overlay. Cursor-centered wheel zoom,
middle/right-button panning, and double-click-to-fit improve placement without
changing the source-image coordinates supplied to detection.
For the hinted fallback, each ruler's bed-map-measured span must agree with the
configured physical span within 2 mm (or 1 percent for larger rulers); a poorer fit
is rejected before it can replace the saved visual reference. Recording or
clearing either reference does not mutate the laser-burned keyed bed map,
configured machine area, or guarded laser limits. An accepted automatic
reference does determine whether support-bound execution is available, and
prepared jobs are bound to its digest and invalidated when it changes.
Automated tests compare calibration and output-review state across detection
and recording and cover the execution-grade/legacy distinction.

The desktop Machine panel now exposes separately tested incremental XY jogging.
Home / park establishes the only accepted starting pose; each request is
translated to an absolute feed-controlled `G1` bracketed by an initial `M5`,
explicit `G21` and `G90`, a configured travel-feed ceiling, and a
planner-completion barrier.
The configured work-area rectangle is intentionally not applied to jogs because
jogging is the operator control used to measure the physical travel envelope.
STOP, disconnect, jobs, controller uncertainty, and motor release invalidate
the tracked jog position, and jogging is unavailable while armed or busy.
Automated tests cover repeated and beyond-configured-area moves, numeric/feed
rejection, UI gating, and a STOP/ACK race. Direction and the selected mechanical
endpoints were exercised on the active GRBL machine; STOP/reconnect behavior
was not physically measured during that session.

The native default workspace uses layout `v7`: Cuts/Layers, Camera, Objects,
Shape, Templates, Trace, Machine, and Material Recipes share one full-height
right column. The lower raw-G-code and Laser/job docks no longer exist, so the
bed/camera workspace reaches the bottom palette and global status area. The
optional Console remains a separate hidden bottom dock. Compatible older window
geometry and active right-tab choices may migrate, but opaque `v6` dock state is
not restored. Templates and Trace expose the shared Generate action below their
Create controls. Preparation and controller execution progress occupy the
bottom status bar, while Connect/Reconnect, Disconnect, deliberately disabled
Pause, and always-available software STOP occupy the non-hideable runtime strip.
The strip reflows at compact widths, and Reset workspace layout recomputes that
responsive row rather than restoring a clipped toolbar. Offscreen production-
theme tests cover `1600x900`, `1080x780`, and `900x680`, including status/progress
containment, compact STOP reachability, zero horizontal inspector overflow, and
a 420-pixel default sidebar. Operation numeric fields still commit once on edit
completion. Interactive desktop review remains pending.

The parked-bed default requests 45 unique frames within eight seconds. This
fits the configured 15 fps deadline (and the 10 fps synthetic source) with
scheduling margin while bounding a 1080p raw burst to roughly 267 MiB.
Median/MAD and jitter analysis screens the full burst; frames that remain
inliers for every mark are ranked for clarity, and final coordinates use the
per-mark median of the best 15 stable frames. Diagnostics persist the consensus
indices and sharpest representative frame. The prior full-inlier median and
single-sharpest-frame behaviors remain configurable. The operator reported the
single-frame experiment initially improving average RMS from about `0.20 mm`
to about `0.12 mm`, but three subsequent home-first 4×4 captures varied from
`0.457` through `0.169 mm` RMS. The consensus default is therefore automated-
test verified only and now needs the same physical repeatability comparison.

GRBL parked-bed calibration captures now scope motor holding to the measurement
window. The service reads and saves `$1`, selects `$1=255` before Home / park,
keeps both axes energized through the final precision frame, and restores the
saved idle delay before CPU-side image analysis. Restoration on capture failure,
repair of a stale continuous-hold configuration, refusal to guess a missing
`$1` before capture, and simulator no-op behavior are automated-test covered. The
physical controller was read-only probed with `$1=250`; the temporary override
was observed holding the axes, but restoring `$1=250` followed by a planner
dwell—and a subsequent `$1=0` forced-idle experiment—did not de-energize them.
The revised release path restores `$1`, then uses FluidNC's explicit `$MD`
motor-disable command when supported. A rejected `$MD` falls back to standard
GRBL `$SLP` and the required soft reset. Either path invalidates the coordinate
reference because loose axes can move, so the next hardware operation must
home. This explicit-disable revision is automated-test verified and its
physical X/Y release remains to be checked.
The first physical `$SLP` release succeeded, but the next Detect action exposed
the documented post-reset alarm as `M5 error:9`. The fallback now clears that
expected alarm after reset, and Home / park can also recover from this exact
pre-home error by unlocking, reissuing `M5`, and immediately running mandatory
homing. Unrelated errors are not suppressed. Repeat detection remains to be
physically rechecked with this recovery. Every new serial GRBL connection sends
`M5` and checks `$1`. If `$1=255` persisted, it restores the configured finite
idle delay; if `$1` cannot be read, connection performs that best-effort finite
restore and then fails clearly instead of trusting unknown state. Ordinary
connection no longer sends `$SLP` plus a soft reset merely to release
already-idle motors, avoiding the controller's audible reset announcement.
Explicit release after powered jobs and held captures retains the `$MD` or
`$SLP`/reset fallback. An abnormal process or power loss can still interrupt
restoration and warrants checking `$1`.

Connect initialization, individual command/ack exchanges, complete Home / park
sequences, and scoped camera holds now have exclusive ownership of the serial
reply stream. The desktop reports **Connecting** and keeps Connect, Disconnect,
and Home / park disabled until initialization finishes; software Stop remains
available. This fixes a physically observed race in which Home / park consumed
the `$1` line from Connect's `$$` query, producing a false missing-setting error
followed by a false `G21 error:2`. Annotated, spaced, and integral-decimal `$1`
reports are also accepted. The race is automated-test reproduced and fixed;
the physical Connect-then-Home / park sequence still needs rechecking.

Controller-required actions now attempt connection themselves when the
controller is offline. This covers desktop jobs, Home / park, jogging,
diagnostics, all parked Machine Setup captures, and the equivalent HTTP command,
positioning, arming, and run routes. A prepared job therefore keeps **START JOB**
available inside its exact Preview. Connection failure is reported as the
operation failure and prevents the requested action; STOP generation
invalidation prevents queued work from reconnecting and continuing. An
untrusted connection after STOP or an uncertain acknowledgement remains blocked
until the explicit reconnect path replaces it.

`tools/live_desktop_driver.py` provides a narrow live Qt diagnostic driver for
named status, Connect, Home / park, and camera-frame operations. It always starts
the real runtime with hardware access enabled and process-wide laser lockout;
there is no option to permit positive laser output or submit arbitrary G-code.
Optional screenshots and JSON output make the exercised live state available
for repeatable diagnosis.

Normalized repeated-object tracing now estimates its shared rounded-rectangle
rotation from populated row-center baselines, then refits regular spacing in
that orientation. This avoids amplifying small `minAreaRect` angle errors from
blurred rounded silhouettes. A separate grid-pose toggle allows direct cells
to retain their observed center and rotation while still sharing canonical
width, height, and corner radius; inferred cells remain snapped to the lattice.
Both modes are automated-test verified and await comparison on the physical
label sheet.

Desktop **Detect objects** no longer assumes a prior Home / park. For a live
machine it starts the temporary stepper hold, homes and parks, selects a fresh
interactive stable frame, restores the original GRBL idle behavior, then
rectifies and analyzes after release. A frozen simulator workspace remains an
immediate copy-only path. The sequencing and simulator bypass are automated-
test verified; after explicit motor release the UI intentionally returns to
**HOME REQUIRED**. The complete physical trace cycle remains to be exercised.

Dense calibration now persists the 5×5 fit, 4×4 interstitial validation, and
shifted confirmation as separate sessions. Each capture action explicitly
selects its matching session, preventing a later 4×4 preparation from making
the 5×5 capture reuse 4×4 targets. This repair is automated-test verified and
was subsequently exercised against the restrained physical marked sheet.

Shifted-confirmation preparation now records mutually exclusive confirmation
metadata. The earlier UI path incorrectly set both validation and confirmation,
causing the matching capture action to reject the job it had just prepared.
Shifted confirmation also uses a narrow 14-pixel seeded search and rejects any
detection shifted more than 10 pixels from its predicted location. The earlier
broad search visibly selected older neighboring grid marks and reported false
errors as large as 16 mm.
Attempting to rescore the original interstitial marks after applying their
refinement now explains that those marks are the refinement's input rather than
an independent check and directs the operator to the shifted-confirmation
capture action by its exact label.

Dense review semantics now distinguish unreliable detections from an accepted
single inferred node. Invalid results expose `rejected_ids`, render those cells
red as REJECTED, and report no inferred IDs; amber INFERRED is used only when
exactly one excluded cell is safely reconstructed and the complete fit passes
all application gates.

The same 2026-08-09 physical setup subsequently completed the current fine and
holdout sequence. The eight-mark fine capture reported an `0.342 mm` translation
scatter and an 8/8-inlier full-bed candidate at `0.136 mm` in-sample RMS. The
independent five-point capture passed at `0.283 mm` RMS, `0.385 mm` maximum,
and mean error X `-0.087`, Y `-0.013 mm`. After the dense results recorded above,
the operator also cut a label perimeter that visually tracked the printed
outline closely. The photograph is useful workflow evidence but was not a
metrology setup, so it does not establish a general physical accuracy limit.

Interactive clarity-sensitive operations are separated from the 45-frame
parked-bed calibration profile. Trace, template matching, color sampling,
workspace capture, and ordinary stable stills now select the sharpest of five
fresh frames after a 0.1-second settle and two-frame discard, with a two-second
deadline. This removes the calibration-burst delay from interactive work while
keeping a small sharpness selection step. It is automated-test verified but its
perceived latency has not yet been timed in the physical UI.

The final 2026-08-09 adversarial software audit hardened the boundaries shared
by those workflows. Machine ownership now serializes reconnect, Home, jog, arm,
and STOP generations; serial reopen discards old acknowledgement state and
bounds receive data; motion-only completion drains the controller planner; and
validated receipt/program integrity is rechecked at the hazardous execution
boundary. Configuration, HTTP requests, project/material/template documents,
SVG, G-code inputs, images, camera frames, calibration evidence, and vision
options now reject duplicate keys, nonstandard constants, coerced types,
non-finite values, malformed containers/topology, and oversized resources before
side effects. Calibration state persists before publication, and generated
G-code/captures use exclusive collision-resistant publication. Release ZIPs use
only tracked regular files, reject every symlink component and checkout escape,
and derive their version from canonical package metadata. Automatic post-job
calibration scoring is bound to the exact start receipt and program digest, so a
stale or merely same-named completed job cannot trigger capture.

The integrated Linux checkout passes all 1476 automated tests. Precision capture
is covered for genuinely fresh unique frames, configurable settling/discard and
burst counts, camera-control readback, sharp-frame selection, temporal
median/MAD rejection, jitter limits, persisted diagnostics, home-first capture,
and guarded no-home recapture. Real C920/V4L2 control readback, capture timing,
vibration settling, jitter thresholds, homing repeatability, and physical
fine-alignment accuracy remain to be verified on the laser machine before these
defaults are treated as proven hardware settings.

Successful powered serial jobs now remain active through cancellation-aware
extended command acknowledgements, a final `M5`, a pre-home planner-completion
barrier, automatic homing, a bounded absolute move to the configured
photography pose, a second motion-completion barrier, normal-idle restoration,
and explicit motor release. At that historical verification snapshot the
default-disabled system did not change fan/coolant state; the active typed Air
Assist implementation recorded above now adds only its explicitly configured
OFF/ON/OFF program behavior and laser-first cleanup. Zero-power jobs and every
stop, failure, emergency, and disconnect path skip the additional homing
and parking motion. The Laser panel distinguishes drain, home, park, and release
phases after stream progress reaches 100%; a terminal background-job error now
raises one desktop alert and is also copied into the in-app machine log.
Every GRBL connection explicitly releases the motors; connection and camera
cleanup repair a persisted camera-only `$1=255` to configured
`machine.grbl_step_idle_delay_ms` (250 ms by default), preventing a stale camera
hold from becoming normal power-on behavior. Tests cover delayed final `M5`
acknowledgement, planner-busy homing rejection until the new barrier, successful
parking, home and park failures, failed powered streams, FluidNC `$MD`, and the
`$SLP`/reset fallback without issuing fan/coolant commands when Air Assist is
disabled. On 2026-08-08 the
operator twice observed the head remain at the last powered point without
homing or parking. Process start time, source timestamps, the loaded profile,
and the generated `M4 S100` job rule out the previously suspected stale process
or disabled completion setting. The prior 3-second job acknowledgement timeout
could expire while a synchronized final `M5` drained motion, and `$H` had no
explicit pre-home planner barrier; test doubles reproduce both paths and their
fixes. The exact 2026-08-08 controller error was not captured. The later
2026-08-16 end-to-end powered acceptance run physically verified post-job
homing, photography parking, explicit release, and terminal Complete on this
rig, while failure-path behavior remains automated-test evidence only.

The final MachineService boundary now rejects non-exact backend and boolean
hardware/motion gates, non-finite or excessive feeds, persistent normal
`$1=255`, and arm timeouts outside 1–600 seconds even when settings bypass the
configuration loader or are mutated after construction. Both Arm and Start
re-parse the exact immutable program lines and recompute their digest,
motion/power flags, and full safety profile. Forged or altered preflight tokens
are rejected before transport output, while STOP, disarm, disconnect, failure,
and scoped motor-release cleanup retain an independent `M5` path. These
guarantees are adversarially automated-test verified; they are not a safety
rating or physical-controller verification.

The object-tracing implementation was consolidated into focused vision,
project-command, desktop-workflow, offline-tool, and artifact-policy commits.
The documentation was then reconciled with that state.

The subsequent Windows portability update selects POSIX serial lazily. Safe
browser and native desktop simulation now start on Windows without loading
`termios`; serial hardware remains unavailable there.

The current desktop update adds a simulation-only, memory-resident corrected
camera source. An operator can load a full-bed PNG/JPEG or generate a selected
template at a known pose, run the normal trace and alignment pipeline on the
frozen frame, and then restore the synthetic camera. This path is unavailable
when hardware access or a non-simulator machine backend is enabled.

The desktop now also owns a native Machine Setup workflow covering configured
camera controls, raw preview, checkerboard lens calibration, manual and
CSV-assisted bed mapping, 5×5 cross-grid detection, residual review, and
workpiece/fiducial checks. It now includes a separate eight-point fine-
registration stage that prepares zero-power or normally guarded powered cross jobs,
classifies multi-point residuals, and can apply only a reviewed global
camera-map translation within a 5 mm cumulative limit. Validated G-code can be
exported from the desktop. The browser remains available, but no operator
capability requires it.

The Machine Setup Lens tab now works against the live camera's exact resolution
and readiness state. It exposes every capture's detection, sharpness, coverage,
region, and exposure evidence; supports confirmed single/all evidence deletion;
and presents structured solve gates with per-view worst errors. Capture, solve,
and model clearing use the shared `AppContext` readiness/invalidation paths.
Cold legacy catalogs now probe only bounded image headers on the calling thread;
pending checkerboard feedback is indexed on a separate worker with detector
inputs capped at `640 x 360`, exact source dimensions retained, visible progress,
and deterministic evidence-mutation/Close guards. That index is advisory only:
the solve re-decodes and detects the selected originals at full resolution.
Index records now require strict finite/type-consistent fields and a SHA-256
content identity; malformed or legacy stat-only rows remain read-only during
status refresh and appear pending. Index and solve read each selected file into
one capped immutable encoded payload and derive the digest, dimensions, and
decoded pixels from those exact bytes. They check path identity around analysis
and compare a newly read final content signature before commit, including for a
same-size and same-mtime replacement. The Lens tab exposes **Re-index all
captures** whenever evidence exists so a ready-looking digest mismatch can be
recovered without deleting captures. Mixed-case PNG/JPEG extensions are
discovered deterministically with lossless same-stem preference. Fresh captures
are atomically staged as lossless PNG and measured through the identical bounded
index pipeline; the table displays exact measurement dimensions so unlike
scales are not presented as directly comparable.
The Camera summary also exposes observed and negotiated FPS.
Replacing or clearing the lens model visibly marks its dependent bed map stale
and disables registration and validation actions until remapping. Compact
offscreen interaction and stale-provenance transitions are automated-test
covered; hardware control readback and a physical diverse-view lens solve remain
unverified.

Machine Setup now runs Home / park, corrected and precision stills, checkerboard
capture and solve, registration/base/dense/validation captures, and workpiece
camera analysis outside the Qt GUI thread as a single owned operation. Its modal
window provides indeterminate progress and its own always-available software
STOP; competing setup actions and Close remain held until worker cleanup
completes. STOP-tainted results are discarded, and starting or failing a new
capture clears prior review/apply state. Offscreen tests cover event-loop
responsiveness, single-flight submission, GUI-thread presentation, STOP result
suppression, request-generation rejection of Home / park queued before STOP,
deferred close, and failure invalidation. Saved axis orientation is
now reported accurately even when bed provenance is stale, while mutation stays
gated on a valid map. Physical motion/camera verification remains pending.

Machine Setup additionally provides an implemented but not yet physically
verified 5×5 dense local-correction workflow for residual error that varies by
bed position. It preserves the current homography underneath, bounds node
movement and local gradients, maps consistently in both directions, and can be
removed independently. A 4×4 interstitial job validates positions not used for
fitting with 0.30 mm RMS and 0.60 mm maximum software gates. Powered 5×5,
4×4, and shifted-confirmation sessions require the current automatic four-corner
support, are generated within both it and the configured machine area, and are
rechecked against the exact support/map binding at Start. These new containment
and Start-verification paths have automated coverage but no physical run.

One coherent failed interstitial result can now produce a reviewed, bounded,
one-time mesh refinement. The refinement is tied to the exact mesh revision and
cannot be applied twice. Final verification uses a separately generated shifted
16-point pattern on fresh material. On 2026-08-09 the physical 4×4 check passed
at `0.273 mm` RMS and `0.475 mm` maximum error, and the shifted confirmation
passed at `0.237 mm` RMS and `0.376 mm` maximum error. Those measurements verify
that session and surface, not general repeatability after a setup change.

Machine Setup now stores explicit X/Y mapping-orientation flags with the bed
calibration and presents unambiguous NORMAL/OFF and REVERSED/ON controls. Fresh
keyed maps record their generated labels as normal automatically. Legacy and
manually labeled maps may remain visibly unrecorded until the operator confirms
them after a laser-off direction check; that confirmation does not mirror
points. The direction/bounds check remains a pre-production hardware check and
does not block the Step 3-to-Step 4 calibration transition. Window geometry, selected tab,
simulation scene, cross sizes, and marking speeds persist in the active data
directory. Marking power intentionally resets to zero each time Setup opens.
All desktop checkbox-based boolean options now use the same compact gray-OFF,
green-ON switch presentation; the Machine Setup X/Y controls use those switches
instead of one-shot reversal buttons.

Machine Setup now also presents the shared controller connection state and a
Connect/Disconnect action above every calibration tab. It uses the existing
guarded `MachineService`; connecting does not bypass hardware authority,
motion, homing, or arming gates.

Fine-registration, accuracy-validation, dense-fit, dense-validation, and
dense-confirmation sessions persist the exact bed homography/residual-mesh
revision active when their mark job is prepared. Powered sessions also persist
the execution-verifiable support signature, four taught corners, and complete
bed-map digest. Capture and offline analysis reject a legacy unidentified
session or any map change; Start additionally rechecks the exact program's
powered segments and current immutable support binding before arming. This prevents an old
powered session from being accepted after a fresh base map, translation,
full-map refinement, mesh change, or support change.

Live camera-refresh errors are now latched before the first modal notification.
The operator acknowledges one Camera unavailable message; timer-driven repeats
remain silent until a frame succeeds or the operator explicitly selects Refresh
camera. Recovery clears the latch and posts a non-modal status notice. This
prevents a camera owned by another application from repeatedly stealing focus.
Explicit Refresh camera now distinguishes a healthy capture from an offline,
frame-less, or faulted one. A failed capture is released and the configured
V4L2 device is reopened asynchronously before the corrected image is retried.
An online camera with a stale, legacy, or otherwise untrusted bed map is now
reported separately as **Bed mapping required** rather than being wrapped in
the exclusive-camera warning. Invalid rectification is rejected before worker
submission, repeats are latched, and the recovery action opens Machine Setup at
Lens or Bed mapping according to the accepted lens-model state. Corrected-view
processing failures from an otherwise healthy camera have their own latched
overlay alert. When the camera is offline, a stale map remains visible as a
status-bar requirement without opening the recovery modal; the prompt is
online-only. Visible overlays are invalidated immediately by focus or mapping
changes, and every in-flight result is identity-bound to the exact lens and bed
models used to produce it; late results are discarded and one replacement is
queued after calibration review closes.

Trace color picking is an explicit canvas state and retains the sampled BGR/Lab
color for neutral targets. The 2026-08-09 physical C920 workflow then exposed a
new regression: Auto selected the bright horizontal seams between the two
columns of dark labels, reported them as a high-confidence `2 × 6` grid, and
placed their centers about half a row pitch from the actual objects. Replay of
that exact saved frame now selects filled global/adaptive contrast hypotheses,
returns 14 observed full label bodies plus the two genuinely occluded top-left
cells as a `2 × 8` grid, and fits about `80.54 × 21.52 mm` geometry. The overlay
edge error on that frame is 0.25 mm median and 0.75 mm at the 90th percentile.
Synthetic adversaries cover dark/light polarity, severe gradients, low
contrast, internal opposite-polarity highlight bands, missing cells, and pose
jitter. Grid numbering is stable row-major. Trace now distinguishes the
configured camera/work crop from the smaller guarded laser-output area after
boundary margin and laser-spot-offset intersection. On the exact saved frame,
all raw right-column observations end inside the X210 camera crop at
X209.75..209.99; shared identical-cell sizing alone moves them at most 0.32 mm
past that crop. The configured 5 mm guard is stricter: ten fitted cells cross
the X/Y15..205 output area by up to 5.32 mm right, 5.51 mm top, and 1.76 mm
bottom, while eight observed contours touch the right/top raster edge and may
be cropped. Those cells remain visible in red and unchecked. Template matching
also excludes them; exact-frame replay retains only six safe detections and
rejects the resulting 37.5% feature coverage instead of accepting a false
16/16 alignment. Fitted rounded-rectangle output now retains the existing
uniform border offset as its default and also supports independent offsets for
the rotated object's Top, Right, Bottom, and Left edges. A one-edge adjustment
moves that edge and its adjoining corners without moving the opposite edge;
focused core and offscreen panel regressions cover rotated top-only trimming,
mode applicability, and option validation.
Trace object creation now defaults to replacing objects created by earlier
Trace captures as one undoable document operation. This prevents a completed
physical workpiece from remaining in the next generated project job, while
preserving drawings, imported objects, and all other non-Trace content. The
operator can disable replacement to accumulate batches intentionally, and the
temporary overlay action is explicitly distinguished from project-object
removal.

Machine Setup Step 3 now provides a parked work-area ruler reference. It holds
the steppers through Home / park and the final raw frame, releases them before
scoring and annotation, then overlays a 10 mm machine grid, orange camera/work
boundary, and green margin/spot-offset-aware output boundary. Full-size Qt
swatches and 40 mm coordinate labels remain readable at 900x680 and 1080x780.
The reference never changes configuration automatically and explicitly keeps
movable honeycomb support separate from the configured calibration/output
rectangles and verified laser reach. The repaired trace and ruler overlay have been reviewed against
the real frame, but the created-object trace has not yet been physically cut.

Home/park setup and park commands now receive a scoped minimum six-second
acknowledgement window for the slow controller observed in the real workflow.
Homing and park-completion waits remain 120 seconds; ordinary streamed job
lines retain the configured `machine.read_timeout`. The first implementation
still required a GRBL realtime `<Idle...>` report after `$H`; the physical
controller completed its double-touch homing cycle but did not provide the
expected report, first exposing a later six-second setup timeout and then the
120-second idle timeout. Home/park now continues from the `$H` acknowledgement
received after the endstop sequence and sends `G4 P0.01` after the park move as
a short positive planner-synchronization dwell. Fine-registration and
accuracy-validation analysis also allows six seconds for the physical bed to
settle after Home/park returns and then waits for three additional frames, with
a separate six-second freshness timeout. This excludes both cached pre-motion
images and fresh frames captured while the slow bed is still completing its
queued park move. This correction is automated-test verified
but still requires a repeated physical Home/park check.

GRBL Home/park now captures the active workspace and the active `G54`-`G59`
and `G92` offsets using read-only `$G`/`$#` queries. Absolute-motion jobs
re-query that state immediately before streaming and are rejected if it changed
after the camera-position reference was established. The snapshot is exposed
in machine status and logged for diagnosis. Focused simulator-backed serial
tests cover unchanged-state acceptance and changed-offset rejection; real
Falcon responses and a physical rejection test remain unverified.

The native window title now begins with the application name, package version,
and a short fingerprint of the installed application files before the project
name. This makes restarted source builds visibly distinguishable; release
packaging can override the fingerprint with
`E3_POSITIONING_SYSTEM_REVISION`. Its application display name is intentionally
empty because Qt/X11 otherwise appends a duplicate product-name suffix to the
complete native caption; the application name itself remains configured.

The desktop now opens a dedicated graphical Preview after project generation,
zero-power framing, and registration/validation job preparation. An immutable
`JobPlan` is parsed from the exact finalized G-code stream and retains
controller-ignored layer/pass/source context, physical laser-spot coordinates,
per-move timing/feed/power, and cut/travel statistics. The Preview provides
scrubbing, animated playback, display-only travel/power/inversion controls,
live move details, warnings, fit/pan/zoom, PNG export, per-operation visibility
and statistics, keyboard timeline navigation, and a dynamic generated-layer
legend. Generation can use source or nearest-path order, and Preview reports
the latter's rapid-travel savings against source order. Raster rows are kept in
fixed serpentine order so individual dark islands cannot be reordered or given
separate acceleration cycles. Imported images are alpha-composited onto white,
sampled at the configured exact physical pitch and scan angle in the active
project frame, and converted with deterministic 8x8 grayscale dithering. Source
minification is area-prefiltered before affine sampling so high-frequency detail
does not depend on source pixel phase or resolution. Source top/mirror/rotation
orientation matches the canvas. Full-row lead-in, white
gaps, and lead-out remain laser-off at engraving feed; desired and spot-corrected
motion are bounds checked. A shared PNG/JPEG/BMP contract bounds encoded files,
dimensions, bit depth, channels, and a conservative 16 MiB decoded footprint
before decode; row/sample/vector-edge/span work and 250,000-command jobs are
also bounded. TIFF is intentionally rejected rather than depending on an
optional Qt image plugin. Generated project jobs carry SHA-256 identities for
their external raster sources, with a Qt-free verifier available to block a
changed or moved asset. Image-only and mixed projects now include
transformed image bounds in zero-power framing, and zero-power raster cut distance
matches the exact unpowered plan for raster, fill, and line output. Nearest-path
ordering falls back to recorded source order above 512 vector paths instead
of entering quadratic planning.
Closed-vector fill, binary vector raster, raster overscan, and configurable
acceleration/command-delay time estimation feed the same exact program model.
A confirmation-gated **Prepare Start Here** action creates a new
bounded absolute-mm program at a reviewed move boundary without starting the
machine. The replacement records the configured controller photography pose,
and its exact plan includes the laser-off physical-spot approach to the selected
boundary after Home/park. Prepared maximum
power is displayed independently from controller execution progress, fixing
the idle-polling presentation that previously replaced a generated power value
with `no active controller job / 0%`. Project revisions invalidate the plan and
close its Preview. The existing generation, homing, arming, validation, and
streaming path is unchanged.

Desktop exact-job preparation is now owner-tokened from snapshot through final
view construction. The project clone and all Qt-free planning/indexing run on
owned workers; authoring is held only while the live document is cloned, and
software STOP remains available. Raw G-code, workspace paths, dedicated Preview
paths, and large backward timeline jumps are built in bounded GUI-thread slices.
Closing an unfinished Preview, STOP, new/open, project revision, renderer error,
or application close cancels acceptance and cannot enable Run/export or clear a
newer renderer's busy state. Application shutdown retains worker ownership and
suppresses callbacks, but drains for at most one second before bounded runtime
teardown under the shared four-second process deadline. Generation no longer
writes an implicit G-code artifact;
explicit export is the only desktop file-write path. Offscreen tests cover 1k
and 100k early close, stale success/failure registration collisions, all three
renderer failure sites, zero-power framing, Start Here, project replacement/shutdown,
250k snapshot responsiveness, and 250k backward scrubbing.

Raster workspace items retain their exact payload-bound SHA across unrelated
document refreshes, eliminating repeated full-file hashes and decodes during
ordinary edits. Cache resolution is budgeted across all unique current project
sources; retained previews downscale as source pressure grows and reload from
their exact payload in cancellable one-source GUI slices when removals make a
larger resolution budget available.
If a source changes in place, the next generation compares its new job
identity with the displayed identity, refreshes the canvas from one exact stable
payload, rejects that result, and requires another Generate/Preview cycle before
Run or export. Tests cover stable-stat same-path mutation, one-read decode/hash
binding, five-source cache residency, zero-reread unrelated edits with a live Qt
heartbeat, sequential live-item/cache rebudgeting with quality recovery, and
first-generation rejection followed by reviewed acceptance. Start Here tests
cover photo-pose metadata, spot-offset approach parity, asset preservation, and
machine program preflight.

## Verified in the current Linux checkout

- **1476 tests passed in 374.33 seconds** with Qt using the offscreen platform,
  including the HTTP security tests with loopback socket access.
- A clean temporary wheel build produced
  `laser_camera_aligner-0.2.0.dev0-py3-none-any.whl` (440,290 bytes, SHA-256
  `0d54ead9f6269afa5205516e17dd019be39d7bc9d803f533fe4f90201256e3dc`).
  Its installed package was byte-identical to the live package tree, both
  entry-point help commands succeeded, packaged web/setup-guide resources were
  present, and the installed simulator produced a `1920 x 1080` frame.
- The tracked-manifest source ZIP contains 210 entries and passes `unzip -t`.
  It excludes local configuration, captures, calibration state, generated jobs,
  logs, caches, and build artifacts. All package/build/install outputs remained
  under `/tmp`; no repository-local artifact was produced.
- Exact-job Preview verification covers final-stream parsing, immutable move
  context, spot-offset recovery, powered-rapid warnings, time scrubbing,
  keyboard timeline navigation, operation visibility, planner comparison,
  fill/raster generation, area-prefiltered grayscale image dithering, alpha handling, canvas-
  consistent source orientation, absolute scan angles, exact non-integral pitch,
  contiguous serpentine rows, image/mixed framing, zero-power metrics, encoded
  and decoded source limits, content-identity invalidation, phase/resolution
  invariance, aggregate pre-iteration overscan/complexity rejection, and the
  large-vector planner fallback,
  guarded Start Here rebuilding, PNG rendering, and separation of prepared
  maximum power from controller progress. The complete dialog was also rendered
  and visually inspected offscreen at 1120 × 760. The mandatory exact-Preview
  -> **START JOB** execution gate was physically verified on the real hardware
  path on 2026-08-16; broader interactive Preview controls and rejection paths
  retain their existing automated or offscreen evidence unless separately noted.
- The icon-only Job toolbar now renders Preview as an original monitor/toolpath
  glyph rather than falling back to the action text. The glyph was rendered and
  visually inspected at high resolution in addition to its Qt mapping test.
- Build-identity verification covers content-sensitive source revisions,
  sanitized packaging overrides, and build-first project window titles.
- Full `E3MainWindow` construction under Qt's offscreen platform displayed the
  build-first identity in the native title bar.
- Focused coordinate-reference verification covers rejection of serial motion
  and arming before homing, successful home/park acceptance, emergency-reset
  invalidation, desktop preflight ordering, X/Y mapping reversal, and the
  hardware/simulation status presentation.
- Stock-layout verification covers schema-preserving Stock boundary
  persistence, exclusion from generated G-code and zero-power frame bounds,
  horizontal/vertical centering, jagged-edge simplification, rotation snapping,
  and fit-with-margin on rectangular and concave stock outlines.
- Focused Trace verification covers the complete button-to-canvas picker state,
  sampled neutral-color acceptance, configured maximum-area rejection, and
  contrast recovery of filled rounded rectangles on noisy wood-like images.
  It also covers global, corrected, adaptive, and signed contrast arbitration;
  bright inter-object seams and opposite-polarity internal highlights; dark and
  light targets; gradients; low contrast; repeated-grid normalization; missing
  cells; stable row-major numbering; retained work-boundary diagnostics; and
  explicit complete-grid selection including inferred cells.
- Focused Home/park verification confirms that setup acknowledgements use six
  seconds, job acknowledgements tolerate planner backpressure with a
  cancellation-aware extended timeout, queued motion drains before `$H`, and a
  delayed synchronized `M5` does not skip post-job completion. Interactive
  commands retain their configured timeout.
- Focused laser-offset verification covers zero-default configuration,
  configured-value loading and excessive-value rejection, desktop and browser
  coordinate correction, zero-power framing correction, camera-aligned preview, and
  rejection when corrected controller motion would leave the work area.
- Focused Machine Setup tests cover native tab availability, safe runtime
  authority, synthetic preview capture, manual bed-point add/delete, and
  explicit axis-reversal and fine-registration controls. They now also cover
  axis-state persistence across mapper/application reopen, legacy-state
  confirmation without point mutation, prominent reversed-state presentation,
  and persistence of non-power setup preferences.
- Focused lens-index tests cover header-only cold status, bounded detector
  inputs, exact-resolution groups, full-resolution solve re-detection,
  malformed/legacy index quarantine, exact-byte digest/decode identity,
  deterministic decode-time A-to-B-to-A replacement rejection during indexing
  and solving, same-stat replacement recovery through forced re-indexing,
  case-insensitive deterministic lossless discovery, fresh/re-indexed PNG
  metric equality, sharpness-scale provenance, atomic external-mutation rejection,
  deterministic capture/delete/clear/solve conflicts, lock-free detector and
  lossless-encode work, progress polling, Qt responsiveness, and deferred
  dialog close. The calibration/imaging/precision-capture/offscreen-Setup
  integration selection passes 128 tests on Linux. A read-only check of the 27
  local legacy `1920 x 1080` JPEGs against the current digest-index schema took
  `0.007794 s`, reported all 27 legacy entries pending, and left the existing
  index bytes unchanged. Before content-digest identity was added, the same
  isolated cold status benchmark fell from the recorded `25.27 s` / about
  `814 MB` peak to `0.0075 s` / `54,128 KiB`; the separate
  bounded background index took `2.909 s` / `147,436 KiB`, produced 27 ready
  entries with 21 preview detections and no errors, and used `640 x 360` inputs.
  Cold status remains header-only with the digest design; the stronger current
  index pass has not been re-benchmarked on that physical evidence set.
- Focused desktop-controller tests cover stale camera callbacks, one-notice
  latching across repeated and changing refresh errors, recovery reset, and
  explicit operator retry, including release/reopen before image refresh.
- Focused fine-registration verification covers bounded target placement,
  laser-off and powered G-code sequencing, zero-power rejection, sparse-cross
  detection, translation classification, position-dependent rejection,
  persistent translation application/reset, the 5 mm cumulative limit,
  seven-inlier full-map acceptance, six-inlier and low-confidence rejection,
  and persistent full-map rollback.
- Focused accuracy-validation verification covers distinct holdout placement,
  fixed-limit pass/fail classification, low-confidence rejection, laser-off
  session rejection, powered synthetic capture, persistence, stale-map
  rejection, and native job handoff.
- Focused precision-capture verification covers configurable settling,
  genuinely fresh discarded frames, unique multi-frame bursts, camera-control
  reapplication/readback, sharp-frame selection, median/MAD temporal outlier
  rejection, per-mark jitter reporting/rejection, home-first capture, no-home
  recapture, and persisted diagnostics.
- Repository-wide Ruff passes on the current Linux checkout.
- Fine-registration capture, reviewed exclusion, and a later 8/8-inlier
  full-map application have been interactively exercised against the C920.
  Independent five-point holdout validation then passed physically with
  `0.258 mm` RMS, `0.417 mm` maximum error, and mean X `+0.019`, Y `+0.078 mm`.

## Physical observation requiring confirmation

On 2026-08-06, a real powered rounded-rectangle job exposed a repeatable-looking
tool-reference displacement. The hardware profile selected GRBL over the
configured serial device, but the exact controller model and firmware identity
were not recorded, so this is **not** a physically verified configuration.

- Configuration at the time: work area X/Y `10..210` mm, boundary margin 5 mm,
  zero software spot offset, `M4 S500`, 2000 mm/min, one pass.
- Generated desired/controller bounds (offset was zero):
  X `86.326..164.326`, Y `115.585..135.585` mm; commanded center
  `(125.326, 125.585)` mm.
- The corrected 4 px/mm camera capture at
  `data/captures/workspace.jpg` placed the new cut center at approximately
  `(97.6, 117.3)` mm.
- Observed spot displacement was therefore approximately
  `(-27.7, -8.3)` mm. A provisional X `-28` mm, Y `-8` mm spot correction was
  tested, but a second cut moved still farther from the target. The later job
  commanded its X center about `+27.7` mm while the observed cut moved about
  `-27.7` mm in the corrected image. The ignored local profile has therefore
  been returned to zero spot offset; the provisional values must not be reused.

The bed map was solved from controller-positioned laser-burned crosses, so it
already references the laser spot. The failed correction instead exposes an
unresolved controller/workspace coordinate-reference problem. The operator
confirmed that Home / park completed before the second job, ruling out omitted
manual homing as the cause of this miss. The near-equal, opposite X response
was strong evidence that saved bed-point X labels were mirrored relative to the
controller. At that stage axis reversal was physically unverified and a
laser-off check was required. That check was not performed; the subsequent
powered result nevertheless confirmed the direction diagnosis.

The operator subsequently applied **Reverse X mapping** and performed a powered
10% rounded-rectangle job on 2026-08-06 at 20:10 despite the requested
laser-off check. The generated bounds were X `55..133`, Y `111..131` mm with
zero software spot offset. In the corrected 4 px/mm capture saved at 20:12, the
new burn is nearly coincident with the intended shaded rectangle. Visual
comparison places the remaining displacement at approximately 3 mm toward
negative X and no more than roughly 1 mm in Y, but overlapping old marks, burn
width, and the manually positioned target make that estimate unsuitable as a
calibration value. This physically confirms that X reversal removed the major
error; it does not yet verify final accuracy or justify a new spot offset.

The next hardware action at that time was a laser-off homed motion review followed by an
independently measured, sparse fine-registration check rather than another
overlapping full rectangle. Do not encode the estimated residual until
controller identity/firmware, work-coordinate offsets, homing state, workpiece
restraint, and X/Y directions are recorded and the displacement repeats at
multiple bed locations.

The first eight-point fine-registration job was physically marked and captured
on 2026-08-06 at 20:39. Seven detector overlays visually matched their crosses;
point 7 was obstructed by the laser head at the photography pose and produced
an obvious false result of approximately X `-8.46`, Y `-12.18` mm. Excluding
that point leaves a proposed camera-map correction of approximately X `+2.67`,
Y `+2.40` mm, but the remaining scatter is `1.23` mm RMS with a `2.24` mm
maximum. That is position-dependent under the current acceptance thresholds,
so no translation has been applied. The review UI now retains explicit Use
checkboxes, permits at most two reviewed exclusions, and moves the corresponding
future target away from the head/park corner.

The fine-registration review now also computes a separate, confirmation-gated
full-bed homography refinement directly from camera pixels and commanded mark
coordinates. It requires seven geometric inliers, broad coverage, bounded
residual/scale/whole-bed movement, and retains the prior solved map for reset.
The latest saved physical recapture at 20:47 was evaluated without applying it.
Its low-confidence review excluded points 2 and 7; of the remaining six, RANSAC
retained only points 1, 3, 4, 5, and 8 and rejected point 6. Its five-inlier
result is therefore refused. A fresh physical run using the relocated point 7
is required; the new full-map apply and rollback controls are automated-test
verified but not physically verified.

A subsequent physical capture at 21:03 detected all eight relocated marks. It
reported a translation candidate of approximately X `+3.019`, Y `+1.512` mm
with `0.613` mm centered scatter, and a full-map fit with 8/8 inliers, `0.262`
mm in-sample RMS, 53% convex-hull bed coverage, and `4.641` mm maximum modeled
bed correction. The operator applied that reviewed full-bed refinement; the
previous solved map is retained in `bed_calibration.json` for reset. This is a
physical application of the workflow, not yet an independent accuracy
verification.

Machine Setup now includes a separate five-point Accuracy validation workflow.
It prepares zero-power or normally guarded powered holdout jobs, binds the session to
the active homography, homes/parks for capture, and automatically reports
per-point, RMS, maximum, and mean error. A pass requires all five confident
detections, no more than `0.5 mm` RMS error, and no more than `1.0 mm` maximum
error. Zero-power-only and stale-map sessions are rejected, and validation has no path
that mutates calibration. On 2026-08-06 at 21:21, an independent powered
holdout capture passed: all five marks were detected, RMS error was `0.258 mm`,
maximum error was `0.417 mm`, and mean error was X `+0.019`, Y `+0.078 mm`.
This verifies the saved camera-to-laser map for that restrained surface,
material height, camera pose, controller connection, and session; it is not a
safety certification or a guarantee after the setup changes.

The local files `label-sheet-test.png`, `trace-preview.png`, and
`trace-result.json` are preserved for the developer who created them. They are
ignored by Git and explicitly excluded from release archives; they are not
test fixtures or product assets.

Use `git status --short --ignored` when those local files need to be audited;
normal `git status` intentionally omits them.

## Product shape

The repository contains:

1. A dependency-light legacy browser application for camera calibration, single-SVG
   placement, G-code generation, and guarded controller execution.
2. A PySide6 desktop application with native machine setup, a native workspace, multi-object
   projects, operation layers, undo/redo, project persistence, materials,
   toolpath preview, and guarded machine controls.
3. Shared camera, calibration, geometry, vision, G-code, and machine services.
4. A native camera-object tracing workflow whose real-camera seam-selection
   failure is reproduced by the exact saved frame and covered by
   synthetic/offscreen adversarial tests; the repaired normalized-grid
   created-object result still awaits a physical cut check. Loose normalized
   grids can retain either each observed center or each detected top edge
   without forcing direct observations onto an ideal lattice.
5. A reusable cutting-template workflow with a versioned library, manual
   selection, geometric candidate ranking, rigid alignment review, and
   undoable project-object creation, plus a dedicated parametric designer for
   regular rounded-rectangle grids and a safe-simulation alignment-image
   workflow.

The desktop is now the primary complete calibration interface. The browser
retains an equivalent single-SVG workflow but is not required for setup.

## Architecture

Browser path:

```text
laser_aligner.__main__
  -> AppContext
  -> AppHTTPServer
  -> web/index.html + web/app.js
  -> SVG placement
  -> gcode.generator
  -> MachineService
```

Desktop path:

```text
laser_aligner.desktop.main
  -> CoreRuntime
  -> AppContext
  -> DesktopController
  -> E3MainWindow / WorkspaceView / panels
  -> ProjectDocument + CommandStack
  -> project.toolpath
  -> MachineService
```

Shared camera/vision path:

```text
CameraService or SyntheticCameraService
  -> cached composed raw-camera-to-bed rectification map
     (lens distortion + bed homography + optional residual mesh)
  -> one cv2.remap interpolation into the top-down bed image
  -> configured machine coordinates or the current rigid honeycomb-local frame
  -> optional memory-only corrected-frame override in safe simulation
  -> workpiece / fiducial / object-trace detection
  -> geometry in the active project coordinate domain
```

Cutting-template path:

```text
rectangle-grid recipe or visible project output objects
  -> normalized cut objects and matching features
  -> versioned .e3template library item
  -> optional deterministic known-pose corrected test frame
  -> manual selection or geometric candidate ranking
  -> reviewed translation + rotation overlay with synchronized canvas controls
  -> one AddObjectsCommand into the active project layer
```

Execution path:

```text
generated G-code
  -> MachineService validation and safety gates
  -> SimulatedTransport or platform serial transport
```

See `docs/ARCHITECTURE.md` for module ownership and persistence boundaries.

## Dated Windows verification snapshot (2026-08-06)

Audit environment:

- Python 3.14
- PySide6 6.11.1

Results:

- **307 tests passed and 2 POSIX-only tests skipped.**
- The complete suite collected, including app simulation and machine-service
  tests.
- Focused template/test-image runs passed their model, library, renderer,
  matcher, controller, widget, workspace, and desktop-integration checks.
- The browser simulator served a healthy API response and its HTML interface.
- The native desktop started with the synthetic camera and simulated controller
  under both Qt's offscreen backend and a native Windows 1600 x 900 visual
  render, ran its event loop, and shut down cleanly.
- An offscreen `E3MainWindow` smoke test saved and reloaded a template, created
  aligned objects as one history command, and undid the operation.
- A second offscreen `E3MainWindow` smoke test drove the modal grid designer,
  saved and edited a template in place, added four rectangles as one history
  command, and undid the entire grid insertion.
- Layout regression tests cover both Save and Update designer actions, compact
  600 x 430 logical screens, 360 px inspector viewports, and 13 pt text without
  hidden horizontal content.
- Generated corrected frames pass the real color/contrast detector and rigid
  matcher at known poses. The desktop controller path recovers a known pose,
  source switching rejects stale camera results, and the 500-feature renderer
  is structurally verified to use local pixel regions instead of full-bed work
  per feature.
- Trace regressions verify that rounded output previews a clean proposed vector
  matching its fitted width, height, rotation, and radius; the analyzed frame
  stays frozen during review; stale callbacks are rejected; and exact or
  simplified contours retain their previewed world placement when created.
  Corrected-image pixel centers are also registered to their OpenCV/BedMapper
  machine coordinates without a half-pixel overlay shift or a non-integral-
  extent scale drift, and ideal discrete rounded masks recover their radius
  without a center-span off-by-one.
- Transient canvas geometry now has a dynamic key: selected Trace results are
  solid green, aligned template cuts are solid cyan, and fixed camera evidence
  is dashed amber. It defaults to the upper-left viewport corner, remains fixed
  during canvas/workpiece interaction, and retains a position set by dragging
  the key directly. Alignment review uses the same smooth fitted camera
  boundary as Trace and keeps both lines visible when they overlap. These key
  positioning and drag paths are covered by offscreen widget tests.
- The native shell has been visually checked on Windows in safe simulation;
  extended manual interaction and real camera/controller use remain unverified.
- Ruff was not available in the current virtual environment.

The cutting-template coverage includes versioned persistence, resilient
catalog scans, compound imported paths, rigid matching, ambiguity and weak-match
rejection, frozen-frame review, cancellation of stale results, transient
overlays, direct-canvas rigid drag/rotation, object creation/undo, generated-job
revision invalidation, strict full-bed image validation, copy-isolated in-memory
source state, deterministic known-pose rendering, source/timer restoration, and
control/badge state. Rectangle width/height/radius edits, regular-grid
generation, editable authoring metadata, exact-ID replacement, gap/pitch
conversion, live preview, and work-area/object-count rejection remain covered.
Toolpath coverage also verifies that microscopic floating-point noise at an
exact work-area edge is accepted while a real overflow is still rejected.

The two skipped tests require POSIX pseudoterminals and `termios`. These results
describe the dated Windows snapshot, not the later Linux branch verification
recorded above.

## Historically verified on Linux

The 0.1.0 release documentation records:

- package compilation and automated tests;
- synthetic camera and automatic bed-map startup;
- a simulated HTTP workflow;
- guarded machine behavior;
- POSIX pseudoterminal serial framing and streamed jobs.

Those claims apply to the earlier release state. They are not evidence that the
consolidated desktop/object-trace branch passes unchanged on Linux.

## Implemented feature set

### Shared core

- Validated JSON configuration.
- Synthetic and OpenCV camera services.
- Linux V4L2 camera control application.
- Checkerboard lens calibration.
- RANSAC bed homography and perspective rectification.
- Workpiece, ArUco, and crosshair-grid detection.
- SVG shape/path parsing, curve flattening, and explicit physical-unit/viewBox
  mapping at the CSS 96 px/in reference conversion. Unsupported CSS
  stylesheets, clipping, masks, and geometry-changing presentation semantics
  are rejected rather than silently flattened incorrectly.
- Bounds-checked vector G-code and zero-power framing.
- Simulator and guarded machine service.

### Browser

- Camera, lens, and bed-calibration pages.
- Automatic and manual bed-point workflows.
- Single-SVG placement, sizing, rotation, and mirroring.
- Workpiece detection.
- G-code generation and download.
- Controller connection, diagnostics, arming, execution, and software stop.

### Desktop

- Native workspace with explicit machine or honeycomb-local coordinates, grid,
  rulers, pan, zoom, snap,
  and a fully adjustable corrected-camera overlay whose control and renderer
  share a 70% default.
- LightBurn-inspired desktop hierarchy with original compact icons, a bright
  drafting bed, a non-hideable responsive runtime/safety strip, always-present
  numeric properties, a full-height design inspector, compact bottom G-code and
  runtime/material docks, and a fixed 30-color operation palette.
- Multiple objects and operation layers.
- Rectangle, rounded rectangle, ellipse, line, vector outline text,
  stencil-safe bridged text, and SVG-path objects.
  Desktop SVG import applies parsed physical dimensions through transformed
  groups, preserves the requested placement center, and stops before object
  creation on any lossy parser warning. Absolute-unit and viewBox-only sizing,
  transformed placement, and fail-closed import behavior are parser- and
  offscreen-desktop-test covered to `0.01 mm`; interactive import review remains
  pending.
- Persistent press-drag-release rectangle drawing with a live active-layer
  outline, endpoint snapping, normalized drag direction, exact-size commit,
  immediate selection, and one-step undo/redo.
- Numeric width, height, and corner-radius editing for a selected rectangle,
  applied as one undoable validated shape change.
- Direct single-object corner resize and rotation handles with live preview,
  anchored-corner resizing, 15-degree Shift snapping, and undoable commits.
- Five-column operation summaries for mode, speed/power, output, and
  visibility, with inline toggles, operation-color editing, ordering controls,
  and scan interval, angle, and raster overscan controls.
- Single-selected-image raster vectorization with original/mask/overlay review,
  physical cleanup and fit controls, hierarchy-preserving compound PATH output,
  one-step Replace or Keep/hide undo/redo, and an automatic visible 0%-power,
  output-disabled Line layer when the active layer is not already appropriate.
- Transform, mirror, duplicate, delete, group, ungroup, align, distribute, and
  z-order commands.
- Undo/redo.
- `.e3laser` save/load, backup, autosave, and recovery.
- SQLite material recipes.
- Multi-layer vector toolpaths, zero-power framing, previews, and estimates.
- Automatic invalidation of generated G-code and toolpath previews after any
  project revision changes.
- Camera focus controls and sharpness measurement.
- Guarded machine connection, park, diagnostics, run, and software stop.
- Native Machine Setup with camera control application, raw preview, synthetic
  scenes, checkerboard capture/solve, manual and CSV-assisted point entry,
  automatic 5×5 grid detection, bed-map solve/residuals, eight-point fine
  registration, workpiece detection, and fiducial inspection.
- Validated generated-G-code export.
- Simulation-only loading or deterministic generation of frozen corrected
  alignment frames, with camera-control gating and a persistent workspace badge.

### Camera-object tracing

- Automatic color/contrast detection.
- Click-to-sample hue.
- Direct and inferred regular-grid detections.
- Analytic fitted rounded rectangles plus simplified and exact pixel-derived
  contours.
- Separate observed and proposed-vector contours, with the workspace preview
  showing the geometry that object creation will consume.
- Border offsets.
- Review and selective conversion to editable project objects.
- Optional post-Create geometric Straighten review for selected, finished,
  non-grid native Cut artwork. The combined object or complete separate-object
  batch is selected automatically, analyzed in current world coordinates, and
  rotated about one shared native-bounds pivot through normal undoable project
  history. Ambiguous evidence, grids, failed native fits, and unrelated objects
  receive no offer; eligible suppressed selections receive a muted reason.
- One captured corrected frame held across detection review, with monotonic
  request cancellation and stale-result rejection.
- One-step undo for a created detection set.
- A **Stock boundary (layout only)** Trace purpose that creates one locked,
  camera-aligned construction outline. The boundary persists in the normal
  project model but is excluded from all laser-output and framing paths.
- A contextual Stock layout toolbar for horizontal/vertical centering, rotation
  parallel to the nearest or named meaningful stock edge, and fit-to-stock with
  an uncut margin. Irregular traced contours are simplified only for edge
  selection; the original stock outline remains authoritative for fit checks.

The trace algorithms and native review lifecycle pass synthetic and offscreen
behavioral tests. Operator-reported Coleman runs now cover successful manual and
Auto native Trace generation, but the controller/firmware/configuration and
measured result were not recorded as formal physical acceptance. Straighten has
not been exercised end to end with the real camera and calibration.

### Reusable cutting templates

- Versioned `.e3template` JSON with atomic, safe-filename library storage.
- Resilient catalog scans that keep valid unique templates available while
  reporting malformed files and excluding duplicate persistent IDs.
- Creation from visible, output-enabled project objects without mutating the
  source project.
- Dedicated regular-grid designer with a live preview, rows/columns, cut
  width/height/radius, and spacing entered as edge gap or center pitch.
- A 500-object grid limit and project-work-area validation before either saving
  a grid template or adding its editable rectangles to the current project.
- Versioned rectangle-grid authoring metadata, preserving template identity
  across parameter edits and distinguishing editable grids from arbitrary
  project-authored geometry.
- Direct creation of a grid in the active project layer as one undoable batch.
- Template-local normalization around the combined cut bounds.
- Per-outer-contour matching features for compound imported SVG paths, with
  contained holes excluded.
- Manual library selection plus synchronized numeric and direct-canvas
  center/rotation adjustment of the complete transient cut preview.
- A role-labeled overlay key and color-independent solid/dashed styling for
  distinguishing aligned cut geometry from camera-detected feature edges.
- Synthetic geometry-based template ranking, weak-match rejection, and
  template/pose ambiguity warnings.
- One corrected frame shared across all candidate trace settings and frozen
  while an accepted overlay is reviewed.
- Safe-simulation loading of corrected full-bed PNG/JPEG images with a strict
  uniform-scale contract and Unicode-safe paths.
- Deterministic corrected-frame generation from a selected template at known
  X/Y/rotation, with optional noise and missing labels; maximum-size grids use
  per-label rendering regions and exact discrete rounded silhouettes that do
  not introduce a detectable antialias fringe.
- One in-memory test frame shared by the workspace, tracer, and matcher, with
  stale-source rejection and explicit restoration of the synthetic camera.
- Rigid translation/rotation placement with scale differences reported but
  never applied.
- New object identities, active-layer assignment, and one-step batch undo.
- Optional `marker_id` schema metadata reserved for future identification.

Automatic matching requires at least three features; one- and two-cell grids
remain available for manual placement only. Matching compares feature centers,
dimensions, and orientation but not rounded-corner radius, so templates that
differ only in radius require manual selection and overlay review.

The portable model/library, generator, and matcher have focused synthetic tests,
and the native controls, review overlay, test-source lifecycle, application,
undo, stale-result handling, and generated-job invalidation have behavioral
offscreen coverage. The generated frame is intentionally idealized and the
workflow has not been verified with real corrected label-sheet images or
physical placement. No marker detector is implemented. See
[docs/CUT_TEMPLATES.md](docs/CUT_TEMPLATES.md).

## Known gaps

### Cross-platform

- No Windows serial backend, hardware camera discovery/control layer, or
  install/launch scripts exist.
- Selecting real serial hardware on Windows fails clearly and directs the user
  back to the simulator.
- Camera hardware handling assumes V4L2 and `/dev/video*`.
- Autosaves and material recipes share an OS-native writable per-user data
  root (XDG/userbase on Linux and LocalAppData/AppData on Windows). Existing
  legacy-root data is copied forward without deleting the source, with fallback
  to the legacy file if migration cannot complete.
- CI covers the portable suite on Windows and Linux; Linux also runs the full
  supported Python-version matrix and repository-wide Ruff.

### Desktop and authoring

- Guarded jogging is implemented and automated-test covered. Direction and the
  selected X5..245/Y5..215 mechanical envelope were operator-exercised, but the
  controller/firmware identity and physical STOP/reconnect response were not
  recorded.
- No tested pause/resume behavior.
- New text is converted immediately to PATH geometry for output. The source
  text, font, mode, height, bridge width, and bridge count are retained as
  metadata, but reopening the text-creation dialog to edit an existing vector
  text object is not implemented yet.
- No DXF import. Raster image import currently stores an external absolute
  asset path; managed or embedded portable assets, selectable dither methods,
  and calibrated grayscale power modulation are not implemented. PNG, JPEG,
  and BMP sources use deterministic ordered dithering; TIFF is unsupported.
  Raster vectorization is single-foreground only, not full-color or multi-layer;
  threshold, source resolution, and smoothing affect its estimated fit quality,
  and final projects retain fitted native line/cubic PATH segments. Preview and
  planning point sequences are bounded transient derivatives.
- Ellipse and line creation remain one-shot centered inserts; only rectangles
  currently have the persistent canvas drawing interaction.
- Single visible, unlocked objects have corner resize and rotation handles.
  Shared multi-selection transform boxes, node editing, proportional resize
  gestures, and smart guides are not implemented. The transient
  cutting-template preview retains its separate rigid-body drag and rotation
  controls.
- No full interactive end-to-end GUI automation.
- Cutting-template matching uses provisional software acceptance gates, but has
  no real-camera validation dataset or physically measured accuracy threshold.
- Object tracing has no sub-pixel edge estimator or real-camera accuracy
  dataset. At the default 4 pixels/mm, one corrected-image pixel is 0.25 mm;
  fitted dimensions and radii remain raster- and threshold-dependent. Highly
  pixel-constrained `A` and `S` glyphs can still vary at narrow counters and
  curved shoulders; requiring a cleaner or higher-resolution source for those
  cases is an accepted first-release quality limitation.
- Loaded test images must already be corrected full-bed views; the loader does
  not infer bed corners or calibrate an ordinary photograph. Generated images
  reuse ideal template geometry and therefore cannot expose lens, homography,
  parallax, material-height, lighting, or mounting errors.
- Automatic template ranking cannot distinguish otherwise identical layouts
  whose only difference is rounded-corner radius.
- `marker_id` is stored but no QR/ArUco/marker identification path consumes it.
- Template placement intentionally supports translation and rotation only; it
  will not scale geometry to conceal calibration or material-height errors.

### Hardware

- GRBL is selected and powered output has been observed, but controller
  identity/firmware and the power scale remain unverified.
- The physical cut response confirmed the historical X-map reversal diagnosis;
  the later fresh keyed map records normal controller labels. Mechanical limits
  were manually probed, but firmware identity, repeatability, and photo-pose
  accuracy remain unverified.
- C920 control readbacks, the lens workflow, and real calibration residuals were
  exercised on the current rig. Repeatability after remounting, material-height
  sensitivity, and parallax remain unverified.
- Powered base, registration, validation, and label-placement output has been
  observed. The latest independent five-point, 4×4, and shifted checks passed,
  and the operator reported a close label-perimeter cut; no metrology-backed
  general placement specification or verified power scale has been established.

## Recommended next sequence

1. Manually exercise the safe native UI on Windows, including loaded and
   generated alignment images, and record usability issues.
2. Add PowerShell setup/launch scripts.
3. Keep the Windows/Linux CI matrix and Linux Python-version coverage green.
4. Separate portable OpenCV capture from Linux V4L2 discovery/control.
5. Keep the complete Linux suite and release-package smoke green.
6. Extend behavioral Qt coverage for the remaining project-editing and
   object-tracing workflows.
7. Validate template matching against curated corrected camera images at known
   material heights and define residual/confidence acceptance thresholds.
8. Verify that release archives continue to exclude local camera/trace output.
9. Only then proceed with documented physical camera/controller bring-up.

## Evidence terminology

- **Tested** — covered by a currently passing automated test.
- **Smoke-tested** — imported or constructed, but not exercised end to end.
- **Implemented, unverified** — source exists but lacks current execution
  evidence.
- **Historically verified** — recorded for an earlier commit or release.
- **Physically verified** — exercised on identified real hardware with recorded
  configuration and results.
