# E3 spare-board firmware 0.1.0

This package is the first **communications-only bench build** for the photographed
Creality CR4NS200141C13 / STM32F401RET6 spare. It contains an SD installation image
with a second-stage USB-serial updater and an inert test application. It does not
contain or overwrite the first 64 KiB Creality bootloader through its own update
code. The original SD loader's actual installation/launch behavior remains a
physical test item. There is no guarantee against bricking.

No Z motion, CR Touch deployment, laser output, heating, or air-assist ON is
implemented. This is not Marlin and must not be configured as the E3 application's
controller. The normal machine and its software are unchanged. Use the spare
with all actuator/output loads disconnected; output states during the original
bootloader and electrical behavior have not been verified. These are not
safety-rated controls.

## First installation through SD

1. Power off the spare and disconnect USB. Remove the temporary BOOT0/TP305
   resistor wiring from the earlier ROM-bootloader experiment. Use normal boot.
2. Verify the target is the **STM32F401RET6** spare, not the installed machine
   board or an F401RC/F103 board. The updater also checks the MCU's F401D/E device
   ID and 512 KiB flash-size register before operating.
3. Copy the package's **SD_CARD/STM32F4_UPDATE** directory to the root of your
   prepared FAT32 card. The directory contains one `e3aux_<hash>.bin` file.
   Leave the filename intact for its first installation. Repeating the identical
   image may require a fresh filename because Creality's loader can remember the
   previous name. Do not copy `application.e3fw` to SD.
4. Insert the card while powered off. Power the spare and allow the stock loader
   to complete. This project provides no reliable display progress indicator:
   do not judge completion from the printer screen or an LED alone.
5. After installation, remove the card with power off, then start the spare
   normally and attach USB to Windows. Confirm the spare's CH340 COM number.
   Close CubeProgrammer, E3, and serial terminals before using the client below.
6. From PowerShell in the extracted package folder, use a Python installation:

   ```powershell
   python -m pip install -r requirements.txt
   python host.py verify application.e3fw
   python host.py inspect --port COM4 --hardware-enabled
   ```

   Replace COM4 if the spare enumerates differently. The maintenance interface
   is **115200, 8 data bits, no parity, 1 stop bit**; CubeProgrammer's previous
   even-parity ROM settings and protocol do not apply here. Expected result is
   `APP` after the five-second updater window, or `UPDATER` if contacted during
   that window. The client accepts only this project's exact firmware identity.
   An opened COM port alone does not establish installation success.

If there is no valid identity, stop and retain the logs. Do not use mass erase,
Read Unprotect, option-byte changes, or try the package on the machine board to
diagnose the spare. SD recovery with the matching stock image is an operator
option only when the stock loader and physical card connection work. This
package does not include a stock firmware dump or a proven recovery procedure.

## Exercise USB updates on the spare

Use the same packaged application for the first update test:

```powershell
python host.py upload application.e3fw --port COM4 --hardware-enabled
python host.py boot --port COM4 --hardware-enabled
python host.py inspect --port COM4 --hardware-enabled
```

The client verifies the image offline, identifies the firmware, enters/holds the
updater, and transfers acknowledged blocks. It never automatically retries an
uncertain write or boots a newly uploaded image. `boot` is explicit. `inspect`
does not move or actuate anything; during the startup window it holds the updater
until `boot` or a power cycle.

There is a five-second updater window at every normal startup. A missing or
invalid application leaves the updater active indefinitely. With a working
application the client requests `UPDATE` to reset into that window. If a future
application crashes, start the spare normally and run the client during the
startup window. This is a designed recovery path, not yet physical evidence.

USB interruptions or power loss can leave the application incomplete. The
updater validates length, vectors, and CRC and writes its validity marker last.
On the next normal boot it should then wait for another upload. It does not keep
two application copies and cannot automatically roll back to the old image.

## Acceptance record before any machine-board use

Record this package's manifest/source hash, board/processor, firmware identity,
COM/USB details, supply, and results. Verify installation and a normal USB update
first. Then, with loads disconnected, separately record recovery from an
interrupted application upload and rejection of corrupted/wrong-board images.
Check that a subsequent valid upload still works and that the stock SD loader
can restore the known package. Application-crash recovery requires a separately
prepared deliberate fault test; this package contains no crashing image.

Output states, UART/clock accuracy, cold boots, SD recovery, and electrical
behavior require physical acceptance. No real hardware was accessed to build or
test this package. Its tests use fake serial/flash and ARM instruction emulation.
Do not install on the working machine until its exact processor, board pin map,
memory layout, stock loader and required behavior are checked independently.

The archive's manifest gives SHA-256 hashes and the compiler/source identity.
It identifies precisely what was built; it is not evidence of physical approval.
