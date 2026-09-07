# E3 spare-board firmware 0.2.0 BENCH

For a spare already running our updater, use the application-only USB update and
[bench console guide](BENCH_GUIDE.md); there is no need to reinstall through SD.
This package adds simulated FAN1/FAN2, probe and Z controls plus real raw PC14
input reporting. Physical fan, motor and probe outputs remain disabled.

The target is the photographed Creality CR4NS200141C13 / STM32F401RET6 spare.
The optional SD installation image combines the unchanged 0.1.0 updater with
the 0.2.0 bench application. It does not
contain or overwrite the first 64 KiB Creality bootloader through its own update
code. The original SD loader's actual installation/launch behavior remains a
physical test item. There is no guarantee against bricking.

No physical Z motion, CR Touch deployment, laser output, heating, or air-assist ON is
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
   `BENCH` after the five-second updater window, or `UPDATER` if contacted during
   that window. The older 0.1.0 application still identifies as `APP`. The client accepts only this project's exact firmware identity.
   An opened COM port alone does not establish installation success.

If there is no valid identity, stop and retain the logs. Do not use mass erase,
Read Unprotect, option-byte changes, or try the package on the machine board to
diagnose the spare. SD recovery with the matching stock image is an operator
option only when the stock loader and physical card connection work. This
package does not include a stock firmware dump or a proven recovery procedure.

## Exercise USB updates on the spare

Use the same packaged application for the first update test. Run each command
separately and proceed only after its success; stop on any error:

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

## Controlled incomplete-upload test

Run this only on the spare, with SD removed and actuator/output loads disconnected,
after a normal upload/boot/inspect and cold startup have succeeded. Keep the known
working `application.e3fw` locally available. This command **erases the existing
application**, writes the first 64 payload bytes, waits for acknowledgment and
stops without committing or booting. The updater and original loader are outside
its allowed erase/program region. Recovery remains a test, not a guarantee.

1. Use the updated source host (older extracted packages may not have this command):

   ```powershell
   python host.py interrupt-upload application.e3fw --port COM6 --hardware-enabled
   ```

   Require exactly `Application deliberately left incomplete after 64 bytes. No commit or boot was sent.`
   Stop and retain output on any error; do not automatically repeat the command.
2. Disconnect both 24 V and USB for ten seconds. With SD still removed, reconnect
   24 V and then USB. Leave all serial clients closed for at least ten seconds
   after startup, then run `inspect`. Expect `UPDATER`. Waiting beyond the normal
   five-second window avoids mistaking the normal startup window for recovery.
3. Once UPDATER is confirmed, use the ordinary `upload` command with the complete
   known application file. Require successful verification/commit, then separately
   `boot` and `inspect`; expect `BENCH` for 0.2.0 or `APP` for the accepted
   0.1.0 recovery image. Stop on any error. A final cold startup
   followed by `inspect` checks that the restored application boots normally.

This tests restart/re-upload after a transfer abandoned between completed flash
writes. It does not test a power cut while erasing/programming, a torn commit,
or recovery from damaged updater/stock-loader bytes. No timing of a cable pull
is needed. Record the image, board, supply, COM port and each observed result.

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
