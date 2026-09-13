# Working Ender startup diagnosis

Latest result, 2026-09-10 10:40:27 UTC: the operator's --resync capture
returned a full custom M115 identity plus ok from kit 08beaf0d, including
mainboard, material-height, compact F401, USB updater and emergency-parser
capabilities. E3HW reports MCU:423 and FLASH_KIB:256. Custom boot and one Pi
USB identity exchange are now physically observed. The old 512 KiB-only
installer is incompatible with this reported capacity. Normal service
integration and an actual USB upload still require operator verification.
Both the passive and line_sync phases were silent; the cause of the earlier
empty-command reply remains unproven. The observations below are historical.

The compact 08beaf0d operator test returned `echo:Unknown command: ""` and
`ok` to M115. The compiled queue/parser accepts normal M115 in emulation;
a NUL-prefixed line reproduces that exact empty-command reply. The real receive
bytes and installed firmware identity remain unconfirmed. For an explicit
follow-up, the standalone diagnostic now accepts `--resync`: after its normal
35-second observation it sends one LF, records one second of replies as
`line_sync`, then sends the normal M115. Identity must appear in the M115 phase;
an earlier reply cannot supply it. Default captures remain unchanged. Service,
hardware-enable, CH340, exclusive-open and receive-limit gates remain in force.
No boot, hold, flash or actuator command is sent by this option.

Update 2026-09-10: the operator restored the stock F401 image using the supplied
SD folder and reports normal boot/menu operation. The supplied photo shows
firmware 2.0.8.26F4, screen V1.0.2 and reported H/W CR-FDM-v24S1_301. SD recovery
and stock boot are now physically observed. At 09:27:25 UTC the operator ran
the diagnostic with the service stopped: CH340 /dev/ttyUSB0, 115200, returned
832 bytes to M115 with Marlin 2.0.8.26F4 and final ok. This establishes one
working stock USB exchange; actual MCU flash capacity and long-run/startup
USB stability remain unknown. The preceding F103 trial produced
the same frozen-screen behavior, with zero received bytes in a Pi diagnostic
that successfully opened CH340 /dev/ttyUSB0. Neither custom image's installation
was independently confirmed. The stock baseline check below is complete for
this run; the next investigation concerns custom F401 image installation,
compatibility and startup. Earlier pending statements are historical.

The September 9 working-board failure is still unresolved. The old stock
2.0.8.26F4 report supports F401, but does not distinguish RCT6 from RET6.
ST specifies [256 KiB for RC](https://www.st.com/en/microcontrollers-microprocessors/stm32f401rc.html)
and [512 KiB for RE](https://www.st.com/en/microcontrollers-microprocessors/stm32f401re.html).
The photographed spare is RET6; that is not identification of the installed board.

Offline inspection of the selected 6991d999 installer confirms:

- SD image size 214620 bytes, loaded at 0x08010000, ends at 0x0804465C.
  That exceeds the 256 KiB part's 0x08040000 end by 18012 bytes.
- The updater accepts only device ID 0x433 and flash-size register 512.
  On rejection it prints `ERR MCU_MISMATCH` once, then services its watchdog
  without reading further serial commands. A late connection can miss the error.
- The archived stock F401 2.0.8.26 image is 166764 bytes and ends at
  0x08038B6C. Its initial stack is 0x20010000. These fit the RC memory bounds,
  but do not prove the installed chip is RC or that stock restoration will work.
- The combined installer's hash still matches its manifest; its updater and
  application layout are internally consistent. Correct files on SD are not
  evidence that the first-stage loader programmed them.

Do not install the F103 candidate to identify this board. Do not repeat the
512 KiB installer until the target question is resolved. No new firmware has
been selected by this investigation.

## First check: independent Pi identity capture

This is a maintenance diagnostic, separate from MachineService's normal
controller ownership. It requires explicit hardware enablement, an inactive
hardware service, a resolved CH340 port and a POSIX exclusive serial open.
It preserves received bytes, listens for 35 seconds, sends one M115 and captures
the reply for 8 seconds. Only an explicit ERR UNSUPPORTED triggers one INFO query.
No boot, update, reset, actuator command, service operation or reconnect is sent.
Opening serial can still affect electrical control lines; no deliberate DTR/RTS
reset pulse is used. Other serial tools must be closed.

Leave the Ender powered, SD removed, USB attached to the Pi and webcam attached.
Disconnect E3 and make laser emission unavailable. This diagnostic temporarily
stops the shared service, so its camera stream also stops; no camera settings
or USB wiring change. This is not the proposed normal startup workflow.

Copy the standalone script from Windows PowerShell (no Git update is required):

```powershell
scp 'C:\Users\lukel\Documents\E3\scripts\diagnose_ender_startup.py' greenhouse-climate@192.168.5.18:/home/greenhouse-climate/diagnose_ender_startup.py
```

Then run in the Pi terminal:

```sh
cd /home/greenhouse-climate/Projects/laser-camera-aligner
sudo systemctl stop e3-hardware-node.service &&
.venv/bin/python /home/greenhouse-climate/diagnose_ender_startup.py --port /dev/serial/by-id/usb-1a86_USB_Serial-if00-port0 --hardware-enabled
```

Keep the complete printed report and private temporary JSON file. Do not power
cycle during this attempt. The script cannot recover a one-time error emitted
before it opened the port, and a silent result cannot identify memory capacity.
Leave the service stopped while interpreting the result. To restore the service
after maintenance, the operator can run `sudo systemctl start e3-hardware-node.service`;
startup will perform its usual read-only readiness queries and subsequent fan-OFF
initialization if ready. A successful identity query alone does not authorize motion.

## Interpret the result once

- `e3_marlin`: the custom firmware answered independently. Use its full version
  and capabilities to identify the installed application, then investigate shared
  service readiness using the same powered session.
- `marlin`: inspect the exact version. A stock report means the desired custom
  application is not answering; it does not by itself prove why SD loading failed.
- `f401_updater_0.1.0` or `f401_updater_0.2.0`: the retained updater is executing.
  Do not issue automatic BOOT or upload; choose the matching recovery path using
  the actual identity. A startup-only banner is historical within this capture,
  not proof that the updater is still active at the end.
- `mcu_mismatch_reported`: the updater's hardware guard rejected the MCU; this
  confirms a mismatch but the old message does not report which ID/capacity failed.
- `no_response`: the E3 RPC layer is not needed to reproduce the silence.
  This cannot distinguish an unbooted image, chip mismatch, MCU/UART fault or
  USB receive failure. Do not keep repeating the same query or change processors.
- Unidentified bytes or another target's identity: preserve the report and stop.

For a silent result, the next operator check is the existing stock F401 recovery
image from `dist/e3-mainboard-v1-6991d999/RECOVERY_STOCK`, using the documented
[stock rollback procedure](../firmware/marlin_mainboard/INSTALL.md#stock-rollback),
then this same identity check once. Its checked SHA256 is
`9a81f7564d62b2b131dcc96f0bf47725e3ee78c3b68b198bb54680f431e02710`.
Restoration replaces the custom updater; USB updates are unavailable while stock
is installed. The intended final custom build still requires USB updating.
Restored stock communication would establish an SD/recovery/serial baseline;
it still would not tell us the MCU flash size. If it also remains silent, stop
firmware trials and investigate the loader/transport/hardware with that result.

## Verification boundary

67 focused Windows tests passed for this diagnostic, the earlier identity helper
and secondary startup. Tests cover allowed queries, silent/unknown/partial
replies, old updater fallback, preserved errors, bounds, service refusal,
exclusive open, close on failure and hardware/platform gates. Ruff and bytecode
compilation passed. Linux execution, POSIX exclusive-open behavior on this Pi,
controller replies, cold startup and stock restoration remain operator-pending.
No controller, Pi service, firmware, SD card or webcam was operated by the assistant.
