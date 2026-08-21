# E3 first-run setup

Generic packages do not contain machine-specific credentials or calibration.
On the first packaged launch, E3 opens a guided setup wizard when no preserved
machine configuration exists.

## Choose a saved machine

First-run requires one concrete saved machine from the selected
built-in machine and physical tool-head profiles. Profiles are reviewed starting
values, not a claim that a controller, machine, laser, or accessory is compatible
or physically verified. The hardware path asks for:

- the Raspberry Pi address, controller/camera bridge ports, and bridge
  credential;
- the usable X/Y work area, with the initial photo position at its center; and
- initial camera resolution and autofocus or manual-focus settings.

The optional **Test network reachability** action only attempts a TCP connection
to the two entered ports. It does not authenticate the credential, identify or
connect the controller, request a camera frame, send G-code, Home, jog, arm,
move, or enable laser output. Saving without running that test also performs no
network or controller action. A successful reachability result is not physical
verification of the machine, motion envelope, camera, calibration, or laser.

## Recovering removed simulator state

Legacy `app.simulation: true`, a simulator controller backend, an active saved
simulator, or a simulator-only registry is handled before credentials are read
or `CoreRuntime` is constructed. Recovery never converts a simulator into a
physical machine and never transfers its camera, calibration, or support
bindings. The recovery page intentionally starts with no selection. The
operator must explicitly choose a configured physical saved machine or choose
to configure a new real machine; simulator-only state offers no implicit
physical choice.

Nothing is changed until **Finish**. A successful Finish writes the replacement
configuration, retires all simulator registry entries, preserves validated raw
physical entries, selects only the operator's explicit choice, and creates an
exact no-clobber backup of the pre-recovery registry. Configuration, registry,
backup, optional credential, and completion marker are one rollback unit. A
failed Finish restores every pre-existing byte and keeps the wizard open.
Cancel performs no write and exits before any runtime, camera, controller,
Home, jog, arming, motion, or output action.

During recovery the optional TCP reachability action is disabled as well, so no
controller or camera endpoint is contacted before Finish. When normal packaged
startup finds the legacy configuration in the replaceable application
directory, Finish writes the repaired configuration to the upgrade-preserved
user configuration path and leaves the application copy unchanged. A config
supplied explicitly with `--config` is repaired at that explicit path instead;
recovery never overwrites an unrelated canonical user configuration. Both the
inspected source and replacement destination are rechecked for concurrent
changes before persistence begins.

An inactive simulator entry alongside an already active physical machine does
not require this wizard. The normal registry loader retains its existing
automatic, atomic retirement and one-time backup for that case.

## Safe saved state

Finishing setup writes the ordinary configuration and the existing
schema-1 saved-machine registry; first-run adds no separate project or machine
schema. The newly created profile snapshot is selected and starts with:

- `machine.allow_motion = false`;
- zero default power and zero frame power;
- low-power framing disabled; and
- no camera-profile or calibration-profile binding inherited from another
  machine.

These software gates are not safety-rated. The physical emergency stop,
enclosure/interlocks, extraction, fire precautions, and operator presence remain
required for hardware use.

## Launch and follow-up setup

On the initial packaged launch, the wizard runs before `CoreRuntime` is created,
so the saved selection becomes the machine for that startup. If **Help > Set Up
Machine…** is available in an already running unconfigured desktop, finishing
the wizard only selects the snapshot for the next launch. A running process
never hot-swaps its controller, work area, material compatibility, camera,
calibration, or execution authority; restart E3 to use a different selected
machine.

For a hardware choice made during initial startup, E3 opens **Machine Setup**
after the main window appears. Review the saved endpoint and dimensions there,
then explicitly perform the applicable focus, lens-calibration, bed-mapping, and
honeycomb-reference steps. First-run creates no calibration evidence. When the
active optical profile has been reviewed, use Machine Setup's explicit **Bind
active profile for a later launch** action to record its camera/calibration IDs
on the saved machine, then restart. Binding itself performs no hardware action
and does not declare the calibration valid. See [Native machine
setup](MACHINE_SETUP.md) and the packaged [Permanent Camera Setup
Runbook](../laser_aligner/operator_docs/PERMANENT_CAMERA_SETUP.md) for the current
operator sequence.

## Persistent files and recovery

The default persistent root is `%LOCALAPPDATA%\E3 Positioning System\` on Windows
and `~/.local/share/e3-positioning-system/` on Linux. First-run stores:

- `config/network-local.json`;
- `data/machines.json`;
- `first-run.json`; and
- `secrets/bridge-token.txt` for a hardware choice only.

Machine configuration, credentials, calibration, templates, materials, and
projects remain outside the replaceable application directory and survive
normal E3 updates. Keep the state root private because the hardware credential
is a secret.

Canceling before **Finish** leaves first-run configuration absent and exits E3;
launch E3 again to retry. During simulator recovery it leaves the legacy
configuration and registry unchanged. Invalid settings or a malformed bridge
address are rejected before the canonical configuration, registry, credential,
backup, or completion marker is written. After a configuration has been saved,
use **Tools > Manage
machines…** for saved profile/endpoint/work-area changes and **Tools > Machine
Setup…** for camera binding and calibration rather than treating the first-run
wizard as physical verification.
