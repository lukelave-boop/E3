# Ender USB idle-failure capture

This is an operator-run diagnostic for the intermittent CH340 receive failure.
Keep the webcam connected and streaming. The assistant must not run hardware
commands: the operator starts the capture, restarts the service if needed, and
runs Inspect. No homing, probing, jogging or laser operation is needed here.

## One bounded attempt

### Current startup failure: capture before service initialization

For the September 2026 mainboard startup fault, start capture **before** the
operator restarts the service. Its startup M106 S0 is the failed exchange;
capturing only a later rejected Inspect/Status would miss it. The Ender remains
physically connected to the Pi. Windows is only an SSH client. Keep the Ender
powered, the SD card removed, the webcam connected and laser emission disabled.
Disconnect the machine in E3 before this diagnostic.

Open an SSH terminal to `greenhouse-climate@192.168.5.18`, then run on the Pi:

```sh
cd /home/greenhouse-climate/Projects/laser-camera-aligner
sudo modprobe usbmon
ender_tty=$(basename "$(readlink -f /dev/serial/by-id/usb-1a86_USB_Serial-if00-port0)")
sudo python3 scripts/capture_ender_usb.py --tty "$ender_tty" --seconds 120
```

Only after `CAPTURE READY`, use a second Windows PowerShell terminal:

```powershell
ssh -t greenhouse-climate@192.168.5.18 "sudo systemctl restart e3-hardware-node.service"
```

The operator restart makes the existing MachineService-owned startup OFF
attempt. It briefly restarts the camera service too; keep the webcam attached.
No Home, Inspect or mainboard control command is needed for this capture.
Let the first terminal finish and retain its printed summary and capture
directory. The three saved files are needed for transfer-by-transfer analysis;
the summary alone cannot distinguish control transfers from serial bulk data.
Correlate the capture's UTC interval with service and kernel journals.

The existing helper was physically exercised in the earlier dwc2 investigation;
this new startup-failure capture is pending operator execution. It does not
require a firmware/software update, USB relocation, or a second serial owner.

### Earlier idle-failure procedure

1. Fetch the reviewed development commit. Extract
   `scripts/capture_ender_usb.py` using `git show COMMIT:scripts/capture_ender_usb.py`
   into a temporary Python file. This does not switch the application checkout.
2. On the Pi, run `sudo modprobe usbmon`. If the previous connection failed,
   restart `e3-hardware-node.service` yourself. Immediately run the helper with
   `sudo python3` and leave that terminal open.
3. Wait for `CAPTURE READY`. In Windows, connect E3 and run the existing
   `probe_diagnostic inspect --host 192.168.5.18` command. Keep its full output.
   If this first check fails, stop the capture and report that result.
4. If it succeeds, leave the webcam streaming and the machine idle for about
   ten minutes, then run Inspect once more. Keep that output too.
5. Press Ctrl+C in the Pi capture terminal. Keep its printed summary and the
   three files in its printed `/tmp/e3-ender-usb-*` directory. The helper stops
   automatically after twenty minutes, 8 MiB of saved records, a device address
   change, or an error. Do not reset the device before stopping this capture.

The saved UTC start/end times identify the interval to retrieve from both the
kernel journal and `e3-hardware-node.service` journal. Those journals are not
collected by the helper. Keep captures and logs out of Git.

## What it records

The standalone standard-library helper opens the binary usbmon observation
device, never the serial port. It issues no controller commands, service calls,
USB resets or power changes. Its Linux-specific imports occur only at use time.
It validates the selected tty's USB vendor/product against this rig's CH340
adapter and fixes capture to that device address.

It requests only 64-byte transfer headers with a null data pointer and zero
payload allocation. Only the Ender's timestamps, directions, byte counts,
statuses and anonymized request IDs are saved. Webcam frames, USB payloads,
setup bytes and kernel addresses are not written to the capture. Files are
private and returned to the sudo user's ownership when capture ends.

The kernel still buffers bus traffic, including camera traffic. Capturing adds
load and can affect timing. The summary totals dropped monitor events and shows
remaining queued events; unavailable statistics are reported as null. Missing
replies cannot establish a failure when the capture is incomplete.

This is a driver-level trace, not a physical USB wire trace. A successful USB
receive does not by itself prove E3 received a complete acknowledged reply.
Correlate it with the same interval's application and kernel logs.

Verification: synthetic Windows tests cover header parsing, privacy filtering,
device selection, bounded capture, loss accounting, and binary API arguments.
Actual capture on this Pi remains unverified. The binary interface is described
in the [kernel usbmon documentation](https://docs.kernel.org/usb/usbmon.html).
