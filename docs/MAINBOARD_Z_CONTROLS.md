# Z controls in the Machine tab

The Machine tab has separate Ender Z controls beside the reported Z height.
X/Y and laser still belong to the primary controller. Choose a 0.1, 1 or 5 mm
step, confirm that the probe is stowed and the path is clear, then use Z- or Z+.
A click requests one bounded move; it does not home or repeat while held.
The Pi reads the actual controller position before calculating the jog target.
Readback is commanded/reported position, not an encoder measurement.

The panel shows the active **Z maximum** and a separate editable value. Apply
saves a value from 20 through 80 mm on the authoritative controller host. It
cannot be lowered below the current known Z. Saving a limit does not move the
axis. For this rig, current border-frame Z20 plus the operator-selected 60 mm
remaining travel gives Z80; the reported collision point was about Z95.

Manual travel stays between Z20 and the configured maximum, at most 5 mm per
request, with laser disarmed, motion enabled, and trusted Z. Moving Z invalidates
the material reference. Probe operations must also fit the configured maximum;
changing the maximum does not expand G39's fixed contact range or change its
Z20 starting clearance. The 30 mm reference remains outside the current probing
range. Z0 must be established at the border, not by homing on material.

While connected and idle, the desktop refreshes Z asynchronously. Unknown,
disconnected, busy or stale readings do not authorize a jog. STOP and session
changes invalidate old readbacks. Unsupported Pi software is reported inline;
there is no fallback to raw serial commands or the primary controller's Z.

## Firmware ceiling and configured maximum

The Pi's configurable maximum is independent of the fixed firmware ceiling.
The Z80 firmware candidate advertises `Cap:E3_Z_LIMIT_80_V1:1`. If this is absent,
E3 still enforces the configured host maximum but displays that the firmware
ceiling is not confirmed. Copying the firmware kit to the Pi does not install it.
The prepared `e3-mainboard-f401-usb-934ef4b3` kit contains the Z80 firmware update;
its application-only USB procedure is separate from the Pi update below.

The Pi persists its maximum in a validated JSON sidecar beside its loaded
configuration, e.g. `config/pi-hardware.json.z-limits.json`, bound to the Ender
port. Windows does not overwrite that authoritative setting with its local
configuration. The default is 80 mm. A malformed sidecar is rejected rather
than silently restoring a higher maximum. These are software motion guards,
not hardware endstops or safety-rated controls.

## Pi companion installation

Use the exact companion with this desktop build. The installer verifies all
previous/source hashes before replacement, preserves unknown edits, makes
backups and leaves service start to the operator. It preserves the installed
startup, compact probing and CPU-cooling behavior. No board firmware is flashed.

Copy the kit from **Windows PowerShell**:

```powershell
scp -r 'C:\Users\lukel\Documents\E3\dist\__PI_PACKAGE__' greenhouse-climate@192.168.5.18:/home/greenhouse-climate/
```

Close/disconnect E3 with the machine idle, then in **Pi Bash**:

```sh
sudo systemctl stop e3-hardware-node.service
sudo systemctl reset-failed e3-hardware-node.service
e3_project=/home/greenhouse-climate/Projects/laser-camera-aligner
e3_z_kit=/home/greenhouse-climate/__PI_PACKAGE__
"$e3_project/.venv/bin/python" "$e3_z_kit/install_z_controls.py" --project "$e3_project" --apply &&
sudo systemctl start e3-hardware-node.service
```

Launch the permanent **E3 DEV TEST** shortcut and connect. Establish primary
Home / park and a valid border Z reference using the existing operator workflow
if required; the Z buttons never perform automatic homing. Check the displayed
maximum, then perform an ordinary 0.1 mm upward/downward test with clearance.
Do not deliberately command a collision to verify the maximum.

## Verification boundary

Automated Qt tests use fake machine services; backend/RPC tests use simulated
transports. Physical Z motion, position freshness during real USB faults, saved
limit persistence on this Pi and the new desktop workflow need operator
verification. Earlier CLI probe/jog results do not establish those new UI checks.
