# Install on the real mainboard

The file inside **SD_CARD/STM32F4_UPDATE/** is the complete installation image.
Copy that **STM32F4_UPDATE folder** to the root of the mainboard SD card.
Do not copy `application.e3fw`, `firmware.elf`, source ZIPs or recovery files
onto the installation card. Use one firmware `.bin` on the card at a time.

Target: the S1 Pro **STM32F401RET6** board. This is not an F103 image and the
retained updater requires the RET6's 512 KiB flash. Do not use it on an F103,
RCT6 or unidentified replacement. The installer loads at 0x08010000 and contains
the retained updater plus the Marlin application at 0x08020200.

1. Save the working controller's M115/M503 reports with its current configuration.
   Keep this entire package, including RECOVERY_STOCK. The firmware reads existing
   EEPROM but does not automatically overwrite it on a version/CRC mismatch.
2. Disconnect the controller session in E3. Power the mainboard off and unplug
   its USB lead so USB cannot back-power it. Leave the webcam attached to the Pi.
   Make the laser incapable of emission for this installation and validation.
   Keep the Creality XY motors disconnected as in the existing Z-only setup.
3. Use the existing working FAT32 SD card, or a FAT32 card with 4096-byte
   allocation units. Put the package's STM32F4_UPDATE folder at its root.
4. Insert the card in the **mainboard** SD slot, not the display. Power the board
   on and leave it undisturbed for at least 60 seconds. Native probe initialization
   can occur at boot, so keep the pin area clear.
5. Power off, remove the SD card, then power on with the controller USB still
   unplugged. Wait at least 30 seconds before reconnecting its USB lead to the Pi.
   This covers the updater's five-second window and Marlin's native probe/display
   initialization (the display startup animation alone takes over ten seconds).
6. Connect in E3. Inspect M115. It must contain both
   `Cap:E3_MAINBOARD_V1:1` and `Cap:E3_MATERIAL_HEIGHT_V1:1`, plus
   `Cap:EMERGENCY_PARSER:1`. The Marlin base version reports 2.0.8.24F4; the E3
   capabilities identify this custom build. M123 should initially report
   `E3MB:1 FAN1:0 FAN2:0 Z_KNOWN:0`.
7. Continue with VALIDATE.md. Firmware support for both fans, probe and Z is
   present immediately after installation. First measurements qualify the
   attached mechanics; do not describe them as already verified by the spare.

The firmware preserves the webcam/Pi arrangement and does not change the Pi's
dwc2 setting. No screen update is required. The stock touchscreen is not the E3
control interface for these added functions.

## Companion application controls

HOST_SOURCE.zip contains matching E3 source, including the new fan/Z CLI and
Pi RPC. Firmware-native commands are available immediately after SD installation.
The new E3 controls also require the matching software on the Pi; that update
has not been run automatically.

For this rig's recorded source installation, the operator software update is:

```sh
set -e
cd /home/greenhouse-climate/Projects/laser-camera-aligner
git status --short
git fetch origin codex/marlin-material-height
git switch --detach a347eb1f949410d948ce14e4fec5b0c9f4e5a2e6
.venv/bin/python -m pip install --no-deps --no-build-isolation --editable .
sudo systemctl restart e3-hardware-node.service
```

This selects the tested implementation, keeps the existing configuration/token,
and preserves the installed camera/OpenCV dependencies. If Git reports a conflict
with local changes, stop without using reset/clean/force. The existing unit must
still use this recorded source directory; do not install into an unrelated folder
while leaving the service pointed at old code. Keep the webcam connected.
The same source supplies the Windows CLI commands in VALIDATE.md. HOST_SOURCE.zip
is the corresponding source archive for inspection or installation through a
separately configured source environment; it does not relocate an existing service.

## Stock rollback

Power down and disconnect controller USB as above. Remove the E3 installer
from the SD card. Copy **RECOVERY_STOCK/STM32F4_UPDATE** to the card root, then
repeat the same mainboard SD installation sequence. This restores Creality's
official F401 2.0.8.26 firmware and replaces the E3 second-stage updater. It does
not erase the factory loader or restore an arbitrary prior EEPROM snapshot.
Compare M115/M503 with the saved reports before returning to prior work.

The rollback `.bin` is byte-identical to the F401 file from Creality's official
2.0.8.26 archive. SHA-256:
`9a81f7564d62b2b131dcc96f0bf47725e3ee78c3b68b198bb54680f431e02710`.
The archived F103 and touchscreen images are intentionally excluded.

If serial traffic arrives during the E3 updater's boot window, it holds in
UPDATER instead of starting Marlin. Disconnect controller USB, power-cycle,
wait thirty seconds, then reconnect. Do not send probe/motion commands to UPDATER.
