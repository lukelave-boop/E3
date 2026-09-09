# E3 mainboard firmware V1

Complete SD-installable firmware for the Creality Ender-3 S1 Pro
STM32F401RET6 / CR4NS200141C13 board, ready for operator physical validation.
It implements real outputs, unlike the earlier BENCH application.

| Requested function | Firmware support |
| --- | --- |
| FAN1 | Independent PC0 PWM, `M106 P1 S0..255`; automatic hotend fan ownership removed |
| FAN2 | Independent PA0 PWM, `M106 P0 S0..255`; existing unindexed `M106 S255` / `M106 S0` remain FAN2 |
| Probe | Native CR Touch / BLTouch handling, PC13 control and PC14 contact; `M280 P0 S10` deploy, `M280 P0 S90` stow |
| Z axis | Native PB6 step / PB5 direction / PC3 shared enable, 400 steps/mm default, normal G28 Z and G1 Z |
| Material measurement | G39, native fast/retract/slow/stow, separate -2 to +10.5 mm border-relative contact interval |
| Readback | M115 capabilities, M119 inputs/trust, M114 position, M123 independent commanded fan PWM and Z trust |
| Emergency parser | Enabled; software stopping is not safety-rated |

The native material cycle, offset/headroom checks, contact failures and host
session safeguards are inherited from `../marlin_material/`. G39 does not move
XY or change the border datum. The +10.5 mm upper contact corresponds to a
12 mm sheet on this rig's support 1.5 mm below the border.

The package combines startup-fixed updater 0.2.0 with the real Marlin
application. Replacing updater 0.1.0 requires this new SD installation. Ordinary
serial traffic no longer holds boot; exact HOLD or validated BEGIN does. The
Pi waits for Marlin identity plus acknowledgment before its separate fan-OFF
exchange. See [connected startup](STARTUP.md) for the operator acceptance test.
This revision has automated verification only until that test is recorded.
Later application updates can use
the retained USB updater. The stock first 64 KiB loader is outside the image.
The package also includes the official F401 2.0.8.26 stock rollback binary,
its checksum/source, and corresponding firmware source. It is a dedicated E3
auxiliary-controller profile; it is not a general printer firmware upgrade.

The companion `machine.mainboard` API and `python -m laser_aligner.mainboard_control`
provide typed fan and bounded Z controls through the existing shared Pi owner.
There is no second runtime serial connection. Native probing and pin controls
continue through `laser_aligner.probe_diagnostic`. Manual Z requires trusted Z,
a stowed-probe confirmation, 20–80 mm absolute target, and at most 5 mm per
request at 300 mm/min. It invalidates the material reference. Both fans are
included in STOP/disarm/disconnect/pre-job cleanup after this profile is identified.
The existing desktop probe buttons are not the new native CLI workflow.

See **INSTALL.md** for copying the SD file and **VALIDATE.md** for physical
validation. Tests and spare-board readback establish software behavior; they
do not substitute for observation of the attached fans, probe and Z mechanics.
The webcam remains connected and part of the working system.

Build from a fresh checkout at the revision in `../marlin_material/baseline.json`:

```text
python firmware/ender_aux/build.py
python firmware/marlin_mainboard/build.py build/marlin-mainboard --core build/platformio
python firmware/marlin_material/run_native_tests.py build/marlin-mainboard --toolchain build/platformio/packages/toolchain-gccarmnoneeabi/bin
python firmware/marlin_mainboard/package.py build/marlin-mainboard --objdump build/platformio/packages/toolchain-gccarmnoneeabi/bin/arm-none-eabi-objdump.exe --updater-bin build/ender_aux/updater.bin --stock-bin build/stock-f401-2.0.8.26.bin
```

Pin references: [Creality source](https://github.com/CrealityOfficial/Ender-3S1/blob/7fffa9270ff8fb5ee208a5b04f832e20bab19c60/Marlin/src/pins/stm32f4/pins_CREALITY_S1_F401RC.h),
[Klipper's S1/S1 Pro mapping](https://github.com/Klipper3d/klipper/blob/master/config/printer-creality-ender3-s1-2021.cfg).
Stock recovery source: [Creality S1 Pro downloads](https://www.creality.com/download/creality-ender-3-s1-pro-fdm-3d-printer).
