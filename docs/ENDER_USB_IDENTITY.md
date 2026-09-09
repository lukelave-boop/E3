# Ender identity through Windows USB

This is an operator-run maintenance diagnostic for the working Ender failing
to answer the Pi. It temporarily isolates the Pi USB/serial path. Normal
application control continues to belong to MachineService; this script is not
a replacement controller backend. The assistant must not run it on the working
machine. The webcam stays connected to the Pi, and this procedure does not
restart or stop its service.

Disconnect the machine in E3, make laser emission physically unavailable, and
leave the Ender powered with its SD card removed. Initially leave its USB on
the Pi, not Windows. Existing Windows serial adapters, including the spare
board, are excluded by the diagnostic's initial device inventory.

In Windows PowerShell:

```powershell
cd C:\Users\lukel\Documents\E3
.\.venv\Scripts\python.exe scripts\inspect_ender_usb.py --hardware-enabled
```

At the prompt, move only the Ender's USB cable from the Pi to this Windows PC.
Wait for Windows to recognize it, then press Enter. The script requires exactly
one new CH340 (1a86:7523); zero or multiple candidates stop before opening any
port. If the Ender was already on Windows when the script started, disconnect
it from Windows before starting the script again.

The script opens the selected port at 115200 baud, listens for 35 seconds
without transmitting, sends M115 once, and receives for eight seconds. Only
an explicit ERR UNSUPPORTED reply causes an INFO identity query, received for
three seconds. It does not send HOLD, BOOT, UPDATE, reset, motion, probe, fan or
heater commands. It does not deliberately pulse DTR/RTS; opening USB serial
can still have electrical effects. The startup interval is a diagnostic delay,
not a product startup requirement. Native firmware initialization is not under
the script's control. The receiver retains startup text and is bounded to
64 KiB. It does not retry failed queries. Serial ownership closes on completion
or failure.

Paste the JSON output or attach its saved `dist/ender-usb-identity-*.json` file.
These local captures are not committed to Git.

- `e3_mainboard_v1_reported`: Marlin acknowledged M115 with both E3 capabilities
  and the emergency-parser capability. This establishes reported identity and
  a bidirectional Windows serial exchange, not attached-hardware qualification.
- `marlin`: Marlin answered, but the complete expected capability set was not
  returned. Read the captured identity before deciding whether flashing worked.
- `e3_updater_0_1_0`: the retained updater answered. The Marlin application was
  not running at the time of these queries; this alone does not identify why.
- `no_response`: no bytes were received during the entire attempt. Investigation
  then includes the board's boot and USB/UART path; this does not prove an MCU
  defect or isolate the USB cable.
- `unidentified` or an error: retain the captured text and do not infer a valid
  firmware identity from partial or unexpected replies.

Verification: 16 Windows automated tests cover device selection/rejection,
startup listening, bounded reception, query selection, missing/stock/updater
responses, short writes, explicit hardware admission and port cleanup. Ruff and
compileall pass. The working-board diagnostic has not been physically run by
the assistant. No firmware or Pi runtime change is part of this diagnostic.
