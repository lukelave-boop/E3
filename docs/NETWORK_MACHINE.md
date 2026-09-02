# E3 Raspberry Pi hardware node

This design keeps project authoring, calibration, vision, Preview, and the first
exact-program preflight on the operator computer while moving persistent job
storage and normal controller execution onto a Raspberry Pi beside the machine.

```text
Windows or Linux E3 desktop
  |-- e3bridge://...  authenticated E3MACHINE/2 job client
  |       -> Pi job store/runner -> one local MachineService -> primary GRBL
  |                              `-> CrealityControllerOwner -> secondary fan
  |
  `-- e3camera://...  authenticated camera RPC
          -> Raspberry Pi -> CameraService -> V4L2/OpenCV camera
```

Direct USB hardware remains available on Linux and keeps the existing local
`MachineService` execution path. For `e3bridge://`, Windows uses
`RemoteMachineService`: it preflights and uploads one immutable job, but the Pi
independently validates it and its Pi-local `MachineService` owns Home/park,
arming, serial writes, acknowledgements, STOP, completion, and local-failure
cleanup. The ownership rule is:

```text
before START is sent:                 Windows owns preparation
after durable ownership_accepted:     the Pi owns execution
after START but before observed state: Windows treats ownership as uncertain
```

A partial upload, a prepared job with no START, and START rejection before local
execution begins are inert. After Windows sends START, a lost or failed response
is ownership-uncertain and must be resolved by querying that exact UUID. After
durable acceptance, closing Windows, losing Wi-Fi/TCP, sleeping the laptop, or
disconnecting a monitor does not stop a healthy job or transition the fan.
Network presence is not a run-enable heartbeat. Explicit STOP acts on primary
GRBL first, including its existing stop/reset and `M5` policy, then runs bounded
independent secondary-OFF cleanup. A secondary ACK timeout or rejection fails
the job while preserving primary `M5`/STOP authority. Sudden Pi process or power
failure can prevent cleanup and result persistence. No interrupted job
auto-resumes.

Before START, an explicit desktop **Disconnect** advances the Windows facade's
operation generation so queued or in-flight preparation cannot later reach
START. When idle, only Disconnect's own cleanup RPC is rebound to that newly
created generation and exactly one `machine.disconnect` action is sent. A later
STOP advances the generation again and retains priority over that cleanup. If a
pre-START upload already owns preparation, or if execution is accepted or
ownership-uncertain, Disconnect instead detaches the monitoring client and sends
neither controller Disconnect nor STOP.

## Transport, dialect, and profile separation

`E3MACHINE/2` is a bounded, high-level JSON protocol. It is deliberately
incompatible with the old raw-serial `E3BRIDGE/1` wire format. The combined
`remote_node` hosts only `E3MACHINE/2`. A new desktop identifies an old node with
a tailored upgrade error; an unupgraded legacy desktop rejects the new node with
its older generic bridge-signature error. Neither direction falls back to
Windows-side powered streaming. The legacy `BridgeServer` module remains
available only for an explicitly separate legacy deployment.

The high-level actions cover capabilities, machine status/setup operations,
upload begin/chunk/finalize, START, active/status/latest/result, STOP, deletion,
and a same-channel scoped stepper hold. Upload and START are separate. Chunks are
at most 64 KiB decoded, each authenticated JSON frame is at most 128 KiB, and a
job is at most 64 MiB. UUIDs and SHA-256 strings must be canonical. A request UUID
has bounded same-boot replay handling, repeated identical upload chunks are
idempotent, FINALIZE revalidates an existing prepared job, and duplicate START
returns the durable state without running it again.

Nodes advertising `pi-execution-policy-diagnostics-v1` also accept a bounded
authenticated diagnostic copy of the fixed execution-policy profile on FINALIZE
and START. The Pi first proves that copy hashes to the existing opaque policy
digest; it never replaces the independently computed Pi-local profile or digest
comparison. On a mismatch the node log records only fixed field labels, never
values, credentials, authorization phrases, arbitrary client labels, or G-code.
The desktop omits the field for older nodes and presents a concise Machine
Setup / Machine Manager guidance message while the same fail-closed rejection
remains authoritative.

The stepper hold is a capture-only lease. A caller must complete Home / park,
realtime-position sampling, and every other ordinary machine operation before
acquiring it. The Pi deliberately keeps `_ordinary_lock` for the full held
session, so an ordinary request on another connection waits until release; this
prevents conflicting motion and is not a supported nesting mechanism. STOP
continues to bypass that lock and can cancel an active hold.

On the Pi, `PiJobService` owns exactly one local `MachineService`, so a remote
client never opens, steals, or interleaves the controller serial port. Status and
STOP remain available during execution; another START, connect/reconnect/
disconnect, Home/park, jog, arbitrary command, realtime-position sample,
calibration motion, and stepper-hold acquisition are rejected. STOP bypasses
ordinary operation/store serialization and invokes the same immediate local
`MachineService.request_stop()` primitive that a future physical Pi input can
call.

The Windows and Pi saved configurations must both specify the same explicit
primary `grbl` or `marlin` dialect and the same work area, guarded output
authority, motion/feed limits, laser power/mode/offset, Home/park behavior, and
other safety profile values. They must also bind the same exact Air Assist
`{mode, fan_index, port, baudrate}` mapping. For `secondary_marlin_fan`, primary
protocol remains `grbl`; Windows retains the Pi-local secondary endpoint as
opaque policy data and never opens it. `protocol = auto` remains available for
direct local serial only when Air Assist is disabled, but
remote controller/upload/monitor operations reject it before network access
because it cannot bind one unambiguous Pi execution policy. No saved profile,
dialect, transport, or authenticated session grants execution authority by
itself.

The saved machine profile describes reusable physical motion-platform defaults,
including controller/transport, envelope, homing, and feed settings. The
separate tool-head profile describes laser/tool defaults such as power
mode/range, feeds, spot offset, and guarded-boundary settings. A saved machine
instance retains the complete validated configuration. None of these profiles
grants connection, motion, arming, laser output, or execution authority.

Desktop first-run and Machine Manager now select from those same existing
profiles. First-run requires a physical choice and may store an
`e3bridge://` controller endpoint and `e3camera://` camera endpoint, but saving
only creates a motion-disabled, zero-default/frame-power machine snapshot with
no inherited calibration binding; it sends no network or controller command.
Choosing another saved machine affects the next launch only. The current
`CoreRuntime`, transport, recipe compatibility, work area, and execution gates
remain bound to the machine resolved when the process started.

This change adds no primary controller or machine compatibility. Physical
bring-up identified the separate Creality/Marlin secondary endpoint and verified
exact FAN2 ON with `M106 S255`; intended OFF with `M106 S0` and the complete
Pi-owned lifecycle remain pending physical confirmation. The secondary mode
never uses a `P` parameter or `M107`.

## Safety boundary

The node is experimental software, not a safety-rated control. The physical
emergency stop, enclosure/interlock, extraction, fire precautions, and operator
presence remain mandatory. An authenticated monitoring-client disconnect has no
machine action after START acceptance: it sends no feed hold, reset, `M5`, or
Air Assist command, and the Pi runner continues the immutable finalized program
and completes its local cleanup. Secondary-assist programs carry exact strict
non-comment `E3AIRASSIST <mapping-sha256> ON|OFF` instructions. The Pi validates
and intercepts them before primary GRBL streaming; the primary never sees them.
This is intentional network independence, not
permission for unattended operation.

A connected red STOP remains effective through `job.stop`. For GRBL an emergency
STOP retains realtime feed-hold/reset policy followed by `M5`; the ordinary stop
path latches cancellation and attempts `M5`. Marlin uses its configured primary
dialect policy. Primary STOP runs first. A bounded independent secondary
`M106 S0` attempt follows without moving STOP behind a secondary ACK, timeout,
or ordinary store operation, but network, Pi, USB, serial, controller, or power
failure can still prevent delivery.
Use the physical emergency stop in an actual emergency.

Controller rejection/alarm, serial write/read failure, acknowledgement timeout,
corrupt committed bytes, local runner exception, and required completion-cleanup
failure stop further streaming, attempt authoritative primary `M5`/STOP followed
by bounded independent secondary OFF, record failure while the service remains
alive, and invalidate controller trust as applicable. A secondary rejection or
timeout is itself a job failure. The sole Pi-side secondary reader also latches
USB hangup/read failure without consuming command replies; the running job checks
that latch between primary program lines and fails before sending further work.
No job resumes
automatically after controller loss. Reconnect and Home/park are required before
later motion or arming.

On Pi service startup, persisted `starting`, `running`, or `stopping` metadata is
atomically changed to `interrupted`; execution authorization is not restored and
the program is never automatically resumed. Startup does not infer primary
controller position or move the machine. Before any primary controller or network
service is opened, the Pi recovers each unresolved, typed secondary binding that
was durably accepted with a job and sends an acknowledged `M106 S0` to that exact
endpoint. Recovery is therefore not redirected if the current saved mapping was
disabled or changed while the node was down. The private record contains only the
bounded mode, target, protocol, port, baudrate, and mapping digest; commands are
reconstructed from the fixed mode and no remotely supplied command text is
deserialized. Bindings are deduplicated, retained across failed restarts and job
retention, and cleared only after matching acknowledged OFF. A malformed binding,
unavailable endpoint, missing acknowledgement, bridge URI, or endpoint that
resolves to the primary leaves recovery pending and blocks later START requests.
After unresolved accepted bindings are reconciled, the current explicitly enabled
secondary mapping independently establishes acknowledged startup OFF. Active
configuration is never rewritten automatically. A process crash or power failure
can prevent software
cleanup while buffered controller work still exists, so the operator must use
the physical stop/interlock, inspect both controllers, reconnect explicitly, and
Home/park before any new run.

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

The first physical bring-up should physically disable laser output when
practical. When using the legacy browser entry point, `--laser-lockout` remains
available as an additional process-level safety override. Validate identity,
upload/finalize/START ownership, monitoring disconnect, reconnect, STOP, Pi
restart/interruption, homing, completion cleanup, camera capture, and calibration
repeatability before any powered test.

## Upload, durable state, and ownership

`PiJobStore` uses the Pi configuration's
`app.data_dir/pi_machine_jobs/{programs,records}` directories. BEGIN maps only a
validated canonical UUID to a server-owned `<uuid>.part` path and atomic JSON
metadata. Sequential bounded chunks
are fsynced; repeated identical bytes at an already committed offset are safe.
FINALIZE requires the exact declared byte count and SHA-256, strict UTF-8, and a
successful Pi-local `MachineService.preflight_program()` over the exact stored
text. It binds program digest, motion/power flags, guarded polygon, and a digest
of the current execution safety profile. A finalize journal makes the validated
`.part` to `.gcode` rename and prepared metadata recoverable across a crash.
Only the atomically committed `.gcode` is runnable.

START reads and hashes the committed bytes again, repeats current local preflight
and every binding comparison, durably writes `starting`, then performs Pi-local
connect/Home/park/arm/start. `starting` is not acceptance. Only after the local
`MachineService` reports a running exact program does the store atomically write
`running`, `ownership_accepted: true`, and `start_accepted_at`; that durable write
is the ownership boundary, and the server then returns START success. If Windows
loses or receives a failed response after sending START, it treats ownership as
uncertain, queries the same UUID until it sees acceptance or rejection, and never
sends a blind second START.

The durable states are `receiving`, `prepared`, `starting`, `running`,
`stopping`, `complete`, `failed`, `stopped`, and `interrupted`. Acknowledged-line
progress is persisted; a line is never counted before its controller ACK. A
fresh Windows process can discover the active job or newest accepted terminal
result by UUID/digest without restarting it. Calibration success receipts are
stricter: only the Windows process that locally created that exact UUID can use
it, avoiding cross-host clock assumptions.

Retention is deterministic: at most eight metadata records, the latest two
terminal G-code files, any current receiving/prepared/active artifacts, and a
24-hour stale-part cleanup on startup. Capacity fails closed when non-terminal
records occupy every slot. Client-supplied paths are never accepted. Remote
status/job records and Pi job-lifecycle logs are bounded and exclude bridge
authentication secrets, authorization phrases, and G-code contents. The
Pi-local `MachineService` controller log still contains the bounded commands it
actually transmits and is deliberately stripped before remote status is returned.

The Windows facade records per-job upload bytes/time/throughput, finalization
time, and START latency in its bounded diagnostic cache. Upload uses 64 KiB
chunks rather than one network request per G-code line. After acceptance,
controller streaming is entirely local and retains the existing one-command,
one-ACK, progress-update loop; no network traffic is required.

## Network trust

Both services use a shared secret from `E3_BRIDGE_TOKEN`. `E3MACHINE/2` performs
mutual HMAC-SHA256 challenge/response, derives direction-specific session keys,
and authenticates every bounded JSON frame with a monotonic counter and HMAC.
The secret is never stored in the E3 JSON configuration or sent as plaintext.
Multiple authenticated clients may monitor, but only the Pi-local
`MachineService` owns the serial device and only one job may be active.

The protocol authenticates integrity; it does not encrypt payloads or replace
TLS and does not protect against every active network attack. Bind it only to a
trusted/firewalled LAN or carry it over a trusted private network/VPN. Do not
expose ports 8765/8766 to the public Internet.

Generate a secret independently on one trusted machine, for example:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Set that same value as `E3_BRIDGE_TOKEN` in the Pi service environment and in
the Windows process environment. Do not commit it.

## Pi configuration

Use the machine's existing verified/local E3 hardware configuration on the Pi.
Its primary controller, secondary controller, and camera devices must remain
Pi-local persistent device paths, preferably `/dev/serial/by-id/...` and
`/dev/v4l/by-id/...`. The exact Pi-local primary GRBL serial path is not yet
confirmed. Do not copy the secondary Creality path into `machine.port`.

A partial Pi override has this shape; the primary placeholder must be replaced
with the already identified GRBL controller rather than guessed:

```json
{
  "machine": {
    "backend": "serial",
    "protocol": "grbl",
    "port": "/dev/serial/by-id/REPLACE_WITH_CONFIRMED_PRIMARY_GRBL",
    "air_assist": {
      "mode": "secondary_marlin_fan",
      "fan_index": 0,
      "port": "/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0",
      "baudrate": 115200
    }
  }
}
```

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
`marlin`, the command-line protocol override is unnecessary. The Pi config must
use its local serial path—not an `e3bridge://` URI—and its writable
`app.data_dir` owns the durable `pi_machine_jobs` store.

The node serves controller traffic on TCP 8765 and camera traffic on TCP 8766
by default. Its default bind address is loopback; `--host 0.0.0.0` is therefore
an intentional opt-in and should be used only on the trusted machine network.

## Windows client configuration

Start from the same current hardware profile that contains the machine's real
work area, guarded output polygon, photo pose, feed ceilings, laser spot offset,
camera resolution, controls, precision-capture values, and Air Assist mapping.
Do **not** replace those values with generic examples.

On the Windows copy, change only the hardware endpoints:

```json
{
  "camera": {
    "device": "e3camera://e3-laser.local:8766"
  },
  "machine": {
    "backend": "serial",
    "protocol": "grbl",
    "port": "e3bridge://e3-laser.local:8765",
    "air_assist": {
      "mode": "secondary_marlin_fan",
      "fan_index": 0,
      "port": "/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0",
      "baudrate": 115200
    }
  }
}
```

These snippets are partial overrides, not a complete hardware configuration.
Use `marlin` only when both sides are the matching verified Marlin profile.
`auto` is rejected for the remote job path. Every remaining safety-relevant
value must match the current Pi machine profile; changing only the endpoint is
the normal deployment pattern.

For this E3 rig, both complete configurations retain primary
`"protocol": "grbl"` and the exact secondary object above. Generated programs
contain strict non-comment `E3AIRASSIST <mapping-sha256> ON|OFF` instructions;
changing the endpoint, baudrate, command mapping, or event schedule changes the
canonical bytes and program digest. The Pi validates and intercepts each
instruction, then its single persistent `CrealityControllerOwner` sends exact
secondary commands and checks ACK/timeouts. Such programs are E3-specific and
are not portable controller G-code; do not submit them outside E3.

The same-primary `marlin_fan` and `grbl_coolant` modes remain available for
other explicitly matched and physically verified machines. Every repository
built-in remains disabled. Do not configure `marlin_fan` or change the primary
protocol to Marlin merely because this rig's separate fan controller is Marlin.
The one Creality serial owner must later be shared with the separate S1
Z-homing/CR Touch implementation, not duplicated.

In PowerShell, set the secret for the E3 process and start the one normal,
hardware-capable desktop. Startup does not connect automatically; keep motion
disabled and laser output physically disconnected when practical during
bring-up:

```powershell
$env:E3_BRIDGE_TOKEN = '<secret>'
.\.venv\Scripts\python.exe -m laser_aligner.desktop.main `
  --config .\config\network-local.json
```

## Camera behavior

The Pi remains the sole owner of `VideoCapture` and Linux V4L2 controls. Normal
snapshots, fresh-frame waits, control application/readback, and precision bursts
are requested remotely. Precision-capture sequence numbers, generation,
control diagnostics, discard/settle metadata, and observed/negotiated FPS are
returned to the desktop.

Desktop shutdown does not reduce these ordinary camera-operation timeouts.
Instead, `RemoteCameraService` resolves addresses in a daemon helper while the
request worker polls shutdown, tracks connecting and active request sockets, and,
only after local shutdown is latched, shuts down and closes them to wake blocked
resolution/connect/send/receive work. Socket creation and natural-completion
races are idempotent; no double-close error escapes.

Remote machine application shutdown is likewise distinct from interactive
Disconnect. Only freshly observed idle state permits one
capability-plus-`machine.disconnect` attempt; stale or empty observer state
detaches without treating the Pi as idle. Resolution, connect, authentication,
capability negotiation, and the action share one absolute maximum 0.75-second
shutdown allowance. Accepted and ownership-uncertain Pi jobs detach without any
RPC or command, including STOP, `M5`, reset, hold, Air Assist OFF, or controller
Disconnect.

For status-wire compatibility only, the desktop accepts the retired
`synthetic` field from a legacy physical Pi node when its value is the exact
boolean `false`. It copies the returned mapping and removes that one field before
constructing the current camera status. Boolean `true`, integer `0`, and every
other value are rejected; this does not restore a synthetic camera or simulation
runtime and does not relax validation of any current status field.

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

The monitor keeps four rates/latencies distinct: **Capture** is the Pi
`CameraService` rate for successfully published usable decoded frames,
**Network** counts complete monitor packets received by the desktop socket,
**Display** counts frames Qt actually presents after latest-frame replacement,
and **Age** is the source-frame age carried by the protocol. The camera's
separate negotiated FPS remains diagnostic backend evidence, not a measured
capture rate.

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

1. Keep laser output physically disabled when practical and launch the normal
   Windows desktop. If using the legacy browser instead, also pass its retained
   `--laser-lockout` safety override.
2. Verify the Pi sees distinct persistent primary GRBL, secondary
   Creality/Marlin, and camera device paths. Record the still-unconfirmed primary
   GRBL path separately; do not substitute the known FAN2 path.
3. Confirm both profiles use the same explicit primary dialect/safety values and
   exact secondary mapping. Connect E3 and verify primary identity/settings
   queries only; Windows must not open the Pi-local secondary path.
4. Disconnect during a partial upload and again after READY but before START;
   verify neither job starts and no output/motion command appears.
5. Home / park with laser output disabled and verify X/Y direction and the
   configured camera pose.
6. START a small laser-off, assist-off job, wait for durable Pi acceptance,
   remove the Windows network entirely, and verify all remaining primary commands
   and the normal completion sequence execute once with no disconnect-induced
   reset, `M5`, or fan transition.
7. Reconnect a new desktop client during a longer laser-off job and verify the
   same UUID/digest/progress without serial interruption or restart; also verify
   a completed-offline terminal result is discoverable.
8. Exercise software STOP during a primary ACK wait, including after reconnect,
   while keeping the physical emergency stop immediately available.
9. With output physically disabled, interrupt/restart the Pi service during a
   job and verify the durable state becomes `interrupted`, the restarted service
   sends no new primary program or motion commands, attempts only acknowledged
   secondary OFF when that mapping was bound, and requires explicit reconnect
   plus Home/park. Separately observe whether an independently powered controller
   continues commands already buffered before the process died; service restart
   cannot recall them.
10. With laser output physically disabled and before enabling the saved mapping,
    verify exact `M106 S0` physically stops FAN2 and record the acknowledgement.
    Exact `M106 S255` ON is already physically observed; OFF remains pending.
11. After OFF is verified, exercise an E3-generated secondary schedule and prove
    that primary GRBL receives no E3AIRASSIST or Marlin line. Check startup OFF,
    requesting/non-requesting layer transitions, normal completion, secondary
    rejection/timeout failure, primary-first STOP with bounded cleanup, Windows
    detach with no fan transition, and Pi restart with no resume.
12. Verify remote camera resolution, FPS, manual focus/exposure/white-balance
   readback, fresh snapshots, and live overlay.
13. Run repeated remote precision bursts and compare jitter/sharpness against a
   direct-Pi/local-camera baseline.
14. Re-run or re-validate lens/bed/support calibration through the remote path.
15. Only after the above succeeds should a small supervised powered sacrificial
    test be considered.

Record the controller identity, firmware, configuration, camera mode, network
path, exact profile digests, and result in `CURRENT_STATE.md`; passing software
tests alone is not physical verification. The Pi-owned execution architecture
has not completed this sequence. Its only new physical evidence is exact FAN2
ON with `M106 S255`; OFF and the full ownership lifecycle above remain pending,
and older raw-bridge results do not verify the new ownership boundary.
