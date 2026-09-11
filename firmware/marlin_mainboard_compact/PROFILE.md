# Compact F401 auxiliary application

## Border-relative Z80 ceiling

The compact profile fixes Z_MAX_POS at 80 mm. A planner buffer_segment guard
calls native kill before queueing nonfinite Z or machine-space Z above 80,
independently of soft-endstop enable state. M115 advertises E3_Z_LIMIT_80_V1.
The existing host manual range remains 20..80 with 5 mm per request. Homing
must establish the border frame; this software limit cannot establish an
unknown physical position or protect against lost steps. The operator measured
about 75 mm upward collision clearance from Z20 and selected 60 mm usable
travel (Z80). That is a measured clearance estimate, not a powered limit test.
Bare G39 retains its V1 contact range and Z20 starting clearance. Parameterized
G39 C<clearance> H<maximum_contact> adds the separately advertised V2 cycle:
clearance 20..80, contact -2..65, and native offset/retract/deploy headroom checks.
Its envelope is scoped to that call, including probe failures; it does not change
ordinary homing, leveling or subsequent V1 calls.

Compact V1 native G28 ends at Z0 (Z_AFTER_HOMING=0). The Pi must recognize
its exact E3_COMPACT_F401_V1:1 capability, verify known Z and the expected
probe input at Z0, then request and verify Z20. Legacy stock remains Z5.
Use the Pi-only kit produced by scripts/package_compact_probe_fix.py to fix
older companions that abort at the Z5 check. This does not change firmware.

This profile retains the native Marlin planner, Z homing, CR Touch / BLTouch,
G39 material-height cycle, both independent fans, readbacks and emergency parser.
It targets the common STM32F401RC/RE subset: Cortex-M4, 64 KiB RAM, application
vectors at `0x08020200` and no application bytes at or above `0x08040000`.
The normal authority for hardware controls remains the E3 MachineService.

The application's USB-update entry is the parameterless, idle-only `M997` in
`marlin_usb_update.inc`. It does not program flash. It transfers to the retained
updater after checking that the MCU, updater vectors and controller state match.
The full first installation must contain both this application and that updater;
`firmware.bin` from PlatformIO alone is an application linked at the higher address,
not the first-install SD image. Use the package builder's SD folder.

The stock touchscreen UI, SD job playback, power-loss job recovery, heaters,
extrusion, filament runout/pause and arc interpreter are removed. In particular,
the screen is not a boot-status indicator for this profile: a Creality splash
screen that remains on the display is expected. USB identity is the acceptance
check. Native probe initialization is retained, but its physical timing and
attached behavior require operator verification.

`M115` advertises the existing E3 mainboard/material capabilities, emergency
parser, `E3_USB_UPDATER_F401_V1`, `E3_COMPACT_F401_V1`, `E3_Z_LIMIT_80_V1` and
`E3_SURFACE_HEIGHT_V2`. Its `E3SG:2` line reports the runtime probe Z offset,
native retract and fixed V2 bounds so the host can bind calibration to the actual
geometry. Its `E3HW:1` line reads
the MCU device ID and flash-size register at query time. These values are not
inferred from the selected image. An unknown ID is still reported by this query;
the updater and M997 impose their own supported-target checks.

Heaters and extrusion have no command handlers in this profile. HAL startup
explicitly drives PA1 and PA7 heater gates and PB4 extrusion STEP low, in addition
to the updater's inactive output setup. FAN1 remains PC0 / M106 P1; FAN2 remains
PA0 / M106 P0. Their kill behavior and GPIO/PWM state clearing are retained.

EEPROM settings remain supported but use distinct schema `E31`. Stock `V83`
settings are rejected and defaults loaded, preventing accidental use of stock
arrays with the extrusion-free layout. Automatic settings initialization/writes
remain disabled. Calibration and attached mechanics are not qualified by a
successful firmware build or by the spare's earlier tests.
The vendor EEPROM presence check remains: it may write the reserved test byte
at address 0x00. This is separate from the settings payload at offset 100.

The source is pinned by `prepare.py`, the complete `compact.patch`, and
`source-files.json`. Preparation rejects an unexpected source revision, changed
reviewed files and unrelated modifications. It was checked on a separate fresh
local clone as well as the prepared build tree.

Windows build and focused checks:

```text
python firmware/marlin_mainboard_compact/build.py build/marlin-mainboard-compact --core build/platformio
python firmware/marlin_material/run_native_tests.py build/marlin-mainboard-compact --toolchain build/platformio/packages/toolchain-gccarmnoneeabi/bin
python firmware/marlin_mainboard/audit_outputs.py build/marlin-mainboard-compact/.pio/build/STM32F401RC_creality/firmware.elf
python firmware/marlin_mainboard_compact/audit_profile.py build/marlin-mainboard-compact/.pio/build/STM32F401RC_creality/firmware.elf
python firmware/marlin_mainboard_compact/audit_planner.py build/marlin-mainboard-compact/.pio/build/STM32F401RC_creality/firmware.elf
python firmware/marlin_mainboard_compact/run_transition_tests.py
```

The native audits execute compiled ARM code with fake GPIO/UART/flash or probe
I/O. They establish software behavior only. They are not physical validation or
a safety rating.
The V2 harness covers strict argument rejection, contact bounds, native headroom,
fast/slow touches, stow and failure cleanup, and a following unchanged V1 cycle.
