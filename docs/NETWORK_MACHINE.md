# E3 Raspberry Pi hardware node

This design keeps the E3 desktop, safety policy, G-code validation, project
model, calibration, and vision work on the operator computer while moving the
physical USB controller and camera onto a Raspberry Pi beside the machine.

```text
Windows or Linux E3 desktop
  |-- e3bridge://...  authenticated controller transport
  |       -> Raspberry Pi -> local POSIX serial -> controller
  |
  `-- e3camera://...  authenticated camera RPC
          -> Raspberry Pi -> CameraService -> V4L2/OpenCV camera
```

Direct USB hardware remains available on Linux. The network path is intended to
make the guarded `MachineService` usable from Windows without adding a native
Windows serial backend. `MachineService` still owns command validation,
arming, motion gates, coordinate-state checks, stop generation, and job
streaming. The Pi bridge is a transport boundary, not an alternate execution
path.

## Safety boundary

The bridge is experimental software, not a safety-rated control. The physical
emergency stop, enclosure/interlock, extraction, fire precautions, and operator
presence remain mandatory. A network disconnect causes the Pi controller
bridge to attempt a GRBL realtime feed hold plus soft reset and `M5` (or Marlin
`M112` plus `M5`). Those requests can still fail if the Pi, USB link, controller,
or power system fails.

No job resumes automatically after a broken controller connection. The desktop
transport reports the failure to `MachineService`; the existing reconnect-only
state invalidates the coordinate reference and requires the normal connection
and Home / park sequence before later motion or arming.

After a STOP/reset, a GRBL-derived controller can remain alarm-locked while the
bridge and settings queries are healthy. During connection normalization only,
an exact terminal `error:9` rejection of `M5` may be recovered with `$X` and a
second acknowledged `M5`, and only when mandatory Home / park is configured.
Connect does not home or move and remains HOME REQUIRED. Any other rejection,
alarm, timeout, disconnect, failed unlock, or failed second `M5` fails closed.

Before a `MachineService` instance has ever established a trusted controller
session, a transient transport-open failure is retried exactly once after the
failed transport is closed and a short delay. Invalid settings, unsupported
protocol/backend choices, and missing or rejected bridge authentication are not
retried. This startup accommodation never applies after a trusted session has
been established; later loss or an uncertain exchange requires explicit
disconnect/reconnect and Home / park.

GRBL Home / park normally relies on `$H`'s terminal `ok`. For compatible
GRBL-derived controllers that omit that reply, E3 also accepts positive
realtime evidence of an active homing state followed by `Idle`. `Idle` without
an observed active transition is not proof of homing. Alarm, error, STOP,
disconnect, or timeout prevents the park move and invalidates the coordinate
reference.

The first physical bring-up must use `--laser-lockout` on the Windows desktop
and should also physically disable laser output when practical. Validate
identity, disconnect behavior, homing, jogging, camera capture, and calibration
repeatability before any powered test.

## Network trust

Both services use a shared secret from `E3_BRIDGE_TOKEN` and a fresh
HMAC-SHA256 challenge before access. The secret is never stored in the E3 JSON
configuration or sent as plaintext during the challenge/response exchange.
Only one controller client can own the serial device at a time.

The transport does not provide TLS encryption or protect against every active
machine-in-the-middle attack. Bind it only to a trusted/firewalled LAN or carry
it over a trusted private network/VPN. Do not expose ports 8765/8766 to the
public Internet.

Generate a secret independently on one trusted machine, for example:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Set that same value as `E3_BRIDGE_TOKEN` in the Pi service environment and in
the Windows process environment. Do not commit it.

## Pi configuration

Use the machine's existing verified/local E3 hardware configuration on the Pi.
Its controller and camera devices must remain Pi-local persistent device paths,
preferably `/dev/serial/by-id/...` and `/dev/v4l/by-id/...`.

The combined node requires a real serial machine backend and an explicit
hardware gate:

```bash
E3_BRIDGE_TOKEN='<secret>' \
python -m laser_aligner.remote_node \
  --hardware \
  --config /path/to/pi-hardware.json \
  --host 0.0.0.0 \
  --protocol grbl
```

Use `--protocol marlin` only for a controller that has actually been identified
as Marlin. If the Pi configuration already sets `machine.protocol` to `grbl` or
`marlin`, the command-line protocol override is unnecessary.

The node serves controller traffic on TCP 8765 and camera traffic on TCP 8766
by default. Its default bind address is loopback; `--host 0.0.0.0` is therefore
an intentional opt-in and should be used only on the trusted machine network.

## Windows client configuration

Start from the same current hardware profile that contains the machine's real
work area, guarded output polygon, photo pose, feed ceilings, laser spot offset,
camera resolution, controls, and precision-capture values. Do **not** replace
those values with generic examples.

On the Windows copy, change only the hardware endpoints:

```json
{
  "camera": {
    "device": "e3camera://e3-laser.local:8766"
  },
  "machine": {
    "backend": "serial",
    "port": "e3bridge://e3-laser.local:8765"
  }
}
```

These snippets are partial overrides, not a complete hardware configuration.
The remaining values must come from the current machine profile.

In PowerShell, set the secret for the E3 process and start the desktop in
hardware mode with laser output locked out for bring-up:

```powershell
$env:E3_BRIDGE_TOKEN = '<secret>'
.\.venv\Scripts\python.exe -m laser_aligner.desktop.main `
  --hardware `
  --laser-lockout `
  --config .\config\network-local.json
```

## Camera behavior

The Pi remains the sole owner of `VideoCapture` and Linux V4L2 controls. Normal
snapshots, fresh-frame waits, control application/readback, and precision bursts
are requested remotely. Precision-capture sequence numbers, generation,
control diagnostics, discard/settle metadata, and observed/negotiated FPS are
returned to the desktop.

Retained frames are JPEG-encoded at high quality for transfer and decoded on
the desktop. E3 computes sharpness on the frames it actually receives. This
extra encode/decode generation must be treated as a calibration change until a
physical repeatability comparison shows it is acceptable. Do not assume an
existing direct-USB camera calibration remains verified merely because the same
physical C920 is used.

### Raw Live Monitor

The native desktop can open a low-latency **Raw Live Monitor** over the existing
authenticated `e3camera://` port. It uses one persistent HMAC-authenticated
socket and the same Pi-owned `CameraService`; it never opens a second
`VideoCapture` or creates a public HTTP camera endpoint. The desktop prefers
the native 1920×1080 size at 10 fps, with 5, 10, and 15 fps choices.

For a local Linux V4L2 MJPG camera on a persistent device path, `CameraService`
first attempts its native V4L2 MMAP reader. Native-size monitor requests then
receive the exact source JPEG without Pi-side resize or JPEG encoding. The same
packet is decoded once for snapshots and precision consumers, with both
representations sharing sequence, generation, and capture time. Unsupported or
failed native initialization closes completely before decoded OpenCV capture is
opened. That transcoded monitor fallback uses 1280×720, 10 fps, and JPEG quality
78; direct mode may use 1920×1080 at 10 fps. Stream metadata and the desktop
visibly report `DIRECT MJPEG` or `TRANSCODED`.

Monitor images are explicitly raw and uncorrected. They do not require or apply
lens correction, bed mapping, overlays, detection, Trace, or calibration, and
they grant no machine-control authority. The server permits at most two monitor
clients, bounds each JPEG to 4 MiB, and samples the newest camera sequence after
every send. The desktop retains one decoded latest frame; slow rendering
replaces it rather than growing a queue. Precision capture remains authoritative
and an established monitor reconnects after interruption.

The Pi and Windows camera profiles should use the same resolution, FPS, FOURCC,
control values, and precision-capture settings; only `camera.device` differs.

## Physical acceptance sequence

1. Keep laser output physically disabled when practical and launch the Windows
   desktop with `--laser-lockout`.
2. Verify the Pi sees persistent controller and camera device paths.
3. Connect E3 and confirm controller identity/settings queries only.
4. Break the Windows network connection during an idle controller session and
   confirm E3 marks the connection untrusted; reconnect and Home / park again.
5. Home / park with laser output disabled and verify X/Y direction and the
   configured camera pose.
6. Exercise small laser-off jogs and software STOP while keeping the physical
   emergency stop immediately available.
7. Verify remote camera resolution, FPS, manual focus/exposure/white-balance
   readback, fresh snapshots, and live overlay.
8. Run repeated remote precision bursts and compare jitter/sharpness against a
   direct-Pi/local-camera baseline.
9. Re-run or re-validate lens/bed/support calibration through the remote path.
10. Only after the above succeeds should a small supervised powered sacrificial
    test be considered.

Record the controller identity, firmware, configuration, camera mode, network
path, and result in `CURRENT_STATE.md`; passing software tests alone is not
physical verification.
