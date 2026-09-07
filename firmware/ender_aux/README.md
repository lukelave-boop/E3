# Retained-loader Ender auxiliary firmware

Version **0.1.0**, experimental STM32F401RET6 / CR4NS200141C13 bench project.
See [UPLOAD_README.md](UPLOAD_README.md) for the operator procedure. SD access
is operator-owned; this project neither repairs the slot nor opens a controller
while building/testing. This is the first upload/update baseline, not a motion
or material-height implementation and not a production Marlin replacement.

## Architecture and flash ownership

| Region, end exclusive | Role | Allowed USB erase/program |
| --- | --- | --- |
| 0x08000000–0x08010000 | Existing Creality first-stage loader, sectors 0–3 | Never |
| 0x08010000–0x08020000 | E3 updater, sector 4 | Never |
| 0x08020000–0x08020200 | Application metadata/commit marker | Only during application upload |
| 0x08020200–0x08080000 | Application vectors, code and data load image, sectors 5–7 | Only during application upload |

Creality is expected to load the SD `.bin` at 0x08010000 and launch that vector
table. This is the published S1-family 64 KiB application offset, **not a
physically verified statement about the spare's installed loader**. Our combined
SD file starts with the updater, pads to the next erase boundary, then includes
metadata and the linked application. It includes no bytes for sectors 0–3.
The STM32 factory ROM is separate and is not changed.

The first-stage loader's SD writes are outside our code's control. Restoring an
ordinary stock SD image can replace the second stage and application, returning
the board to stock behavior. Our uploader cannot update the updater itself or
program option bytes, OTP, read protection or the original loader. These software
restrictions are not hardware write protection against arbitrary future firmware.

The updater does not depend on the application's health to expose its serial
protocol after a normal startup: five-second command window, or indefinite wait
when application validation fails. Any received traffic holds it. A valid image
has a checked length, board/format IDs, vector bounds, header CRC and payload CRC.
CRC detects corruption, **not maliciously authored firmware**. No signatures or
firmware authenticity claim. Both updater and app normalize inherited startup
state; flash work is performed in RAM to handle single-bank flash stalls.

The known step/enable/heater/fan pins are configured inactive and probe control
is left without deployment pulses. Neither stage ever enables motion, PWM,
positive laser, air-assist ON or a probe operation. Exact GPIO mapping is based
on the published S1 configuration and still requires physical verification.
The first-stage loader's output behavior is outside our control.

This standalone maintenance project is isolated from `laser_aligner`, Qt, HTTP,
the desktop project pipeline and the browser SVG pipeline. `host.py` is an
operator-run flasher for the disconnected spare, not an alternative runtime
controller path. All serial operations require an explicit port and
`--hardware-enabled`; offline validation is the non-hardware path. Any future
motion integration belongs behind MachineService and requires its existing
admission gates and new focused acceptance/rejection and physical tests.

## Build on Windows

Install/extract Arm GNU Toolchain **14.2.Rel1** for `arm-none-eabi`. The initial
build used the official Windows archive:

`arm-gnu-toolchain-14.2.rel1-mingw-w64-i686-arm-none-eabi.zip`

SHA-256: `6facb152ce431ba9a4517e939ea46f057380f8f1e56b62e8712b3f3b87d994e1`

Set `ARM_GCC_BIN` to its `bin` folder, pass `--toolchain <folder>`, or put the
compiler on PATH. The build also discovers an extracted toolchain under the
repository's ignored `build/firmware-tools` directory. No PlatformIO, external
HAL, online dependency fetch or proprietary IDE is needed at build time.

From the repository root, using the repository Python:

```powershell
python firmware/ender_aux/build.py
python -m pip install -r firmware/ender_aux/requirements.txt
python -m pytest -q tests/test_ender_aux_host.py
python firmware/ender_aux/run_core_tests.py
python firmware/ender_aux/run_platform_tests.py
python -m ruff check firmware/ender_aux tests/test_ender_aux_host.py
python -m compileall -q firmware/ender_aux
```

Build output goes under ignored `build/ender_aux`. The upload folder and ZIP go
under ignored `dist/ender-aux-0.1.0-<sourcehash>`. The binaries, ELF/map files and
package must not be committed. Source/manifest hashing identifies the exact
tested deliverable separately from the repository base revision.

## Protocol

115200 8N1, ASCII, newline terminated (LF or CRLF), at most 255 characters before
LF. There is no G-code transport compatibility and no automatic controller
reconnection. A host sends one command and waits for its complete response.
The command set and exact replies are in `protocol.c`; example update:

```text
INFO
E3AUX1 UPDATER 0.1.0 BOARD=0401E013
HOLD
OK HOLD
BEGIN 0401E013 <8-hex payload-length> <8-hex payload-crc32>
OK BEGIN
DATA 00000000 <4-to-64 payload bytes as hex>
OK DATA <8-hex next byte offset>
END
OK END
BOOT
OK BOOT
```

BEGIN validates metadata before erasing sectors 5–7. DATA offsets must be
sequential; block bytes are programmed and read back. END checks payload/vector
integrity, writes header words, then the magic/commit word last. A failed block
or malformed command aborts the session; start a new transaction explicitly.
No host command supplies a flash address or sector index. INFO/HOLD do not erase.
Application supports INFO/M115, M5 (output-off acknowledgement), and UPDATE only.

The `.e3fw` file has a 512-byte header. Seven little-endian uint32 words:
magic `0x55413345`, format 1, board `0x0401E013`, payload byte count, payload CRC32,
image-format version 1, CRC32 of bytes 4 through 23. Remaining header bytes are
0xFF. Payload starts with vectors at 0x08020200 and is padded to four bytes.
Both CRCs use the standard reflected IEEE/zlib CRC32. The vector stack must be
8-byte aligned in 96 KiB SRAM, and the Thumb reset vector must be within payload.

## Verification and remaining work

Automated tests must run before presenting an upload package: ARM GNU compile
and linker assertions; ARM-executed C protocol/image tests with fake flash;
production flash-wrapper tests with simulated registers; Python image/host
fake-serial tests; Ruff and compileall. Hardware register
sequences, timing, serial signal integrity, original-loader compatibility and
physical recovery are not emulated by those tests.

Production motion, CR Touch measurement, fans, persistent machine settings and
Marlin behavior are intentionally future stages. No accepted application image
can presently operate the machine. Retained-loader recovery is implemented as
a design and pending operator bench evidence, not claimed proven.

Primary references:

- [ST RM0368](https://www.st.com/resource/en/reference_manual/rm0368-stm32f401xbc-and-stm32f401xde-advanced-armbased-32bit-mcus-stmicroelectronics.pdf): flash sectors, registers, SRAM, GPIO and USART.
- [STM32F401RE datasheet](https://www.st.com/resource/en/datasheet/stm32f401re.pdf): exact RET6 device and electrical limits.
- [Klipper S1/S1 Pro board configuration](https://github.com/Klipper3d/klipper/blob/master/config/printer-creality-ender3-s1-2021.cfg): 64 KiB offset, USART1 and board pin mapping.
- [Katapult deployer](https://github.com/Arksine/katapult#katapult-deployer): alternative replacement approach; **not used** here. This project supplies a dedicated second stage retaining the first loader.
