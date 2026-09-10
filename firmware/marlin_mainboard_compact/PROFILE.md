# Compact F401 auxiliary application

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
parser, `E3_USB_UPDATER_F401_V1` and `E3_COMPACT_F401_V1`. Its `E3HW:1` line reads
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
