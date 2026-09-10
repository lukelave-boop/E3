# Compact F401 mainboard with USB updates

This experimental replacement is built for the S1 F401 pin mapping and supports
the exact ID/capacity pairs F401RC 0x423/256 KiB and F401RE 0x433/512 KiB. The
complete installer fits within the 256 KiB layout and uses at most 64 KiB RAM.
It addresses the previous installer's 512 KiB-only guard/oversized image and
the missing native-Marlin-to-USB-update workflow. Neither was established as
the installed board's exact failure cause. Physical acceptance is still required.

The first SD installation includes retained updater 0.3.0, the native application
and the explicit M997 USB maintenance entry. Later application updates use the
Pi's existing USB cable; no SD access or ST-Link is needed for those updates.
The factory loader's first 64 KiB is outside the installer and USB write range.
An update interrupted before commit leaves the updater available for restoration;
this is recovery, not automatic rollback to a previous application.

## What runs

- FAN1/PC0 via M106 P1 and FAN2/PA0 via M106 P0; unindexed M106 remains FAN2.
- Native CR Touch handling, Z homing/movement and bounded G39 material measurement.
- M123 fan command/Z trust report and the emergency parser.
- M115 reports E3_MAINBOARD_V1, E3_MATERIAL_HEIGHT_V1,
  E3_USB_UPDATER_F401_V1 and EMERGENCY_PARSER capabilities, plus
  `E3HW:1 MCU:<hex ID> FLASH_KIB:<capacity>`.
- EEPROM settings retain a distinct E31 schema. Incompatible stock settings use
  compiled defaults, without automatically initializing or saving settings.

**The stock touchscreen/menu is disabled in this build.** Its display may remain
on its own startup screen. Judge installation using the USB identity report,
not whether the Creality menu appears. Display support can be added separately.
SD job reading, heaters, extrusion, filament/pause handling, arcs and power-loss
job resume are also removed. The factory SD firmware-install procedure still
works. Heater gates and extrusion STEP are explicitly driven inactive.

The Pi service remains the normal controller owner. No ordinary runtime command
enters the updater. M997 is admitted only as a sole idle command with no queued
motion, active probe operation or fan/heater targets. It shuts outputs down and
enters the updater; the host then explicitly holds and verifies it before erase.
Normal boot retains the five-second update window despite incidental serial
traffic. Only deliberate HOLD/validated BEGIN prevents automatic boot.

## One-time SD installation

1. Disconnect E3, stop its Pi service and keep laser emission unavailable.
   Keep the existing Z-only wiring and probe clearance. Native probe
   initialization may move the pin; attached mechanics are not yet qualified.
2. Turn the Ender off and disconnect its USB cable for SD programming.
   Keep the webcam physically connected to the Pi.
3. Remove other firmware installers from the FAT32 SD card. Copy the
   **STM32F4_UPDATE folder inside SD_CARD** to the card root. The file is
   `STM32F4_UPDATE/__SD_NAME__`. Do not copy the outer SD_CARD directory.
4. Insert it in the mainboard's SD slot, power on and leave it undisturbed
   for at least 60 seconds. Power off, remove the card, reconnect USB to the Pi,
   then power on. Normal startup keeps USB attached.
5. Run the identity check below. Do not attempt homing, probing or a job yet.

## Pi support and identity check

From Windows PowerShell, copy this extracted kit:

```powershell
scp -r 'C:\Users\lukel\Documents\E3\dist\__PACKAGE_NAME__' greenhouse-climate@192.168.5.18:/home/greenhouse-climate/
```

On the Pi:

```sh
sudo systemctl stop e3-hardware-node.service &&
cd /home/greenhouse-climate/__PACKAGE_NAME__ &&
/home/greenhouse-climate/Projects/laser-camera-aligner/.venv/bin/python install_pi_support.py --project /home/greenhouse-climate/Projects/laser-camera-aligner --apply &&
/home/greenhouse-climate/Projects/laser-camera-aligner/.venv/bin/python diagnose_ender_startup.py --port /dev/serial/by-id/usb-1a86_USB_Serial-if00-port0 --hardware-enabled
```

The support installer changes only the startup reader so it recognizes this
updater while waiting for Marlin. It checks known original/replacement hashes,
backs up the original, preserves unknown local changes by refusing them, and
does not restart services, touch configuration, or reinstall dependencies.
The stopped service's camera stream is temporarily unavailable during maintenance.
Keep all USB wiring attached after SD programming.

The diagnostic listens 35 seconds, then queries M115. Require reported_role
`e3_marlin`, all four capabilities above with value 1, the E3HW line and final
`ok`. Expected hardware pairs are MCU 423 / FLASH_KIB 256 or MCU 433 / FLASH_KIB
512 (leading hexadecimal zeroes may vary). The reported version base is
2.0.8.24F4; capabilities and hardware line identify this compact build.

An updater identity alone, stock Marlin, MCU_MISMATCH or silence is not a pass.
Preserve the full report. A mismatch remains queryable instead of printing once
and becoming silent; `DIAG` also reports hardware while in the updater. No
physical chip marking, sensor state or flash success is inferred from USB
enumeration. No automatic BOOT or motion is sent by the diagnostic.

After successful identity acceptance, the operator may restart the service:

```sh
sudo systemctl start e3-hardware-node.service
```

Verify E3 mainboard status reports both fans commanded off and Z unknown before
continuing existing attached-hardware validation. Cold-start ordering and actual
outputs must still be observed; software readbacks are not physical measurements.

## Later application-only USB update

Disconnect E3 and stop e3-hardware-node.service before this explicit maintenance
operation. Both fan commands must already be zero and the mechanism idle. Use
only the host.py and application.e3fw for board ID **0401C013**; old RET6 BENCH,
old full-size F401 and F103 images are deliberately rejected.

From the kit directory on the Pi, run one command at a time:

```sh
/home/greenhouse-climate/Projects/laser-camera-aligner/.venv/bin/python host.py upload application.e3fw --port /dev/serial/by-id/usb-1a86_USB_Serial-if00-port0 --hardware-enabled
/home/greenhouse-climate/Projects/laser-camera-aligner/.venv/bin/python host.py boot --port /dev/serial/by-id/usb-1a86_USB_Serial-if00-port0 --hardware-enabled
/home/greenhouse-climate/Projects/laser-camera-aligner/.venv/bin/python host.py inspect --port /dev/serial/by-id/usb-1a86_USB_Serial-if00-port0 --hardware-enabled
```

Upload must say verified and committed; it does not automatically boot. After
boot allow normal initialization before inspect, which must report MARLIN_F401.
Do not proceed after an error or automatically retry a failed transaction.
Interrupted-upload testing belongs on the spare, not the assembled working rig.

## Stock recovery

The existing, operator-proven stock F401 2.0.8.26 image is in RECOVERY_STOCK.
Use its STM32F4_UPDATE folder and the same power-off/mainboard-SD procedure
above. Remove competing .bin files first. Its SHA256 is
`9a81f7564d62b2b131dcc96f0bf47725e3ee78c3b68b198bb54680f431e02710`.
Stock recovery restores the stock menu and replaces the custom USB updater.
It does not validate custom motion behavior or restore arbitrary saved settings.

## Source and verification

Exact patched Marlin and updater sources are in firmware-source.zip, with ELFs
and a manifest for this package. The application build starts from Creality
7fffa9270ff8fb5ee208a5b04f832e20bab19c60 using pinned PlatformIO 6.1.18,
ststm32 12.1.1 and the existing pinned dependency set. The updater uses the
repository's Arm GNU 14.2.Rel1 freestanding toolchain.

Automated checks cover ELF bounds/vector handoff, the native G39/probe code,
compiled output-off writes, both supported MCU/flash pairs, flash-region
protection, commit-last recovery, mismatch diagnostics and the complete
host/M997 update protocol with simulated transports/registers. They do not
simulate real SD loaders, power loss during active flash operations, signal
integrity or attached mechanics. This exact package has no physical acceptance
yet and these software controls are not safety-rated.
