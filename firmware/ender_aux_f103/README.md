# Experimental F103RET6 mainboard and USB updater

This separate candidate targets **STM32F103RET6, 512 KiB flash / 64 KiB SRAM**
with the Creality S1 28 KiB factory bootloader. It includes independent FAN1/FAN2,
native CR Touch / Z control, G39 material-height measurement, and USB firmware
updates from its first SD installation. It is not an F401 installer.

**This is not the recommended installer for the currently failing Ender.**
The historical working-board record in `docs/MATERIAL_HEIGHT.md` and
`CURRENT_STATE.md` reports stock Marlin **2.0.8.26F4** during successful probing.
Unless that board was subsequently replaced, the evidence supports F401.
The operator currently cannot inspect the chip marking. Do not use alternative
firmware as a chip-identification test. No physical F103 installation or validation
has been performed, and this candidate is not a fix for the F401 startup failure.

## Layout and USB recovery

| Region | Address |
| --- | --- |
| Existing factory loader, outside the installation image | 0x08000000–0x08007000 |
| Retained F103 updater | 0x08007000–0x08010000 |
| Application metadata, CRC and commit marker | 0x08010000–0x08010200 |
| Application including ST's 484-byte vector/reserved table | 0x08010200–0x08080000 |

The updater runs on Cortex-M3, uses USART1 PA9/PA10 through the existing CH340
USB connector, and only erases application pages. Flash writes run from SRAM,
use F103 halfword programming, verify results, and commit the header last.
Device ID 0x414 and independently 512 KiB flash are required for updater writes.
Neither USB erase nor programming can write the factory loader or the updater.
No mass erase or option-byte modification is used.

Ordinary traffic does not hold the five-second automatic boot deadline. Exact
HOLD or a validated BEGIN deliberately enters maintenance. An incomplete or
invalid image remains in recovery. Protocol/image checks detect accidental
corruption; this is not signed firmware authentication or safety-rated control.
Power loss while actually programming, clock tolerance, stock-loader behavior,
electrical output levels and attached mechanics remain physically unverified.

## First installation, only after target identification

Use the board-specific factory SD procedure, with the laser incapable of emission
and probe clearance available. The package's `SD_CARD/firmware.bin` goes at the
SD card root for this F103 target; there is no STM32F4_UPDATE folder in this
package. Shut down the board and remove USB back-power for the installation.
Allow the SD installation to finish, then power down and remove the card.
Normal subsequent startup keeps the Ender USB and webcam connected to the Pi.
Retain a known matching stock F103 recovery image before an operator test;
the F401 recovery file from earlier packages is not an F103 rollback.

The package includes `pi-readiness.patch` for the companion Pi source at 5f52e40
or its documentation-only descendant. It teaches the existing serial owner to
wait through the F103 updater identity instead of rejecting it. The update is
operator-run; it does not change configuration, serial ownership or normal
startup timing. Apply with `git apply --check` before `git apply`; do not force
over conflicting local code. Install the companion source and restart its service
once as in the existing firmware handoff. The source patch has automated Windows
coverage; this change has not been deployed to or physically tested on the Pi.

Require M115 to report all of:

- `Cap:E3_MAINBOARD_V1:1`
- `Cap:E3_MATERIAL_HEIGHT_V1:1`
- `Cap:E3_USB_UPDATER_F103_V1:1`
- `Cap:EMERGENCY_PARSER:1`

Initial M123 should report both fan commands zero and Z unknown. Base firmware
version is 2.0.8.24F1. Continue with the existing mainboard physical validation
procedure only after firmware identity and configuration are checked. Do not
assume a firmware ACK verifies actual movement, stopping, or probe deployment.

## Later USB updates

No SD card or ST-Link is required for application updates after initial install.
Use this package's `host.py`; F401 application files and maintenance clients are
not interchangeable. The host validates an image before opening serial and
requires `--hardware-enabled` for every hardware operation.

Maintenance requires an idle machine, heater targets zero, both fan commands
OFF, and no active probe cycle. Establish those conditions through E3. Stop the
Pi hardware service before using the maintenance client so it has exclusive
serial ownership. This also pauses the node-owned camera for maintenance;
leave its USB cable attached. No service stop is required for normal boot.

With the matching package extracted on the Pi, use its existing environment:

```sh
E3_PY=/home/greenhouse-climate/Projects/laser-camera-aligner/.venv/bin/python
# Operator: first stop e3-hardware-node.service with the machine idle.
"$E3_PY" host.py inspect --port /dev/serial/by-id/usb-1a86_USB_Serial-if00-port0 --hardware-enabled
"$E3_PY" host.py upload application.e3fw --port /dev/serial/by-id/usb-1a86_USB_Serial-if00-port0 --hardware-enabled
"$E3_PY" host.py boot --port /dev/serial/by-id/usb-1a86_USB_Serial-if00-port0 --hardware-enabled
# Operator: restart the Pi service and verify firmware identity in E3.
```

The client requires the F103-specific capability, then requests M997. Marlin
rejects M997 with arguments, queued movement, SD printing, active probing,
positive fan commands, nonzero heater targets or invalid updater vectors.
Accepted maintenance disables heater/fan/motor outputs before entering the
retained updater. The host then deliberately holds it before erase. Upload
verifies and commits the application but does not automatically boot it.
Do not run the destructive `interrupt-upload` recovery test on the working machine.

## Build and verification

In the E3 repository, prepare an isolated checkout of Creality revision
7fffa9270ff8fb5ee208a5b04f832e20bab19c60. Existing F401 sources/builds are preserved.
Use PlatformIO 6.1.18 and the pinned ststm32 12.1.1 platform:

```text
python firmware/ender_aux_f103/build.py --marlin-source build/marlin-mainboard-f103-candidate --core build/platformio
python firmware/ender_aux_f103/run_core_tests.py
python firmware/ender_aux_f103/run_platform_tests.py
python firmware/ender_aux_f103/run_transition_tests.py
python firmware/ender_aux_f103/run_native_tests.py build/marlin-mainboard-f103-candidate --toolchain build/firmware-tools/bin
python firmware/ender_aux_f103/package.py build/marlin-mainboard-f103-candidate
```

The build scripts never open serial or flash hardware. The bundle includes exact
patched Marlin and updater source, ELFs, the USB application image and hashes.
Executable checks use emulated ARM code and fake I/O. They are not an electrical
model of an STM32 board and do not qualify physical recovery or motion.

Sources: ST RM0008, ST PM0075, ST cmsis-device-f1 `stm32f103xe.h`, and the pinned
Creality `pins_CREALITY_S1.h` / `HAL_STM32F103RET6_creality` environment.
