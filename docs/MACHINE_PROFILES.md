# Machine profiles and saved machines

## Status

The versioned saved-machine registry and reusable machine/tool-head profiles are
now the foundation for first-run setup, Machine Manager, immutable runtime
identity, material-recipe compatibility, and new-project authoring defaults.
They do not create a second controller path or replace the existing guarded
runtime authority.

At browser and desktop startup, `CoreRuntime` creates or loads
`<data_dir>/machines.json`. When that file does not exist, E3 atomically records
one `existing-machine` entry containing an exact copy of the already validated
`machine` and `laser` sections from the active JSON configuration, but only when
the configuration has an explicit endpoint. `SELECT_CONTROLLER_PORT` and the
former implicit `/dev/ttyUSB0` default require setup instead of creating a
plausible registry. Once a registry exists, its active saved-machine snapshot is
the runtime authority passed to `AppContext`; raw JSON machine/laser values do
not override it.

The desktop exposes the registry through Machine Manager. Changing the saved
active-machine ID selects the next launch only; it does not reconnect, Home,
move, arm, or hot-swap the immutable current runtime. A malformed registry is
rejected before the desktop constructs `AppContext`; E3 never guesses which
saved machine the operator intended.

## Data model

### Machine profile

A `MachineProfile` describes reusable motion-platform defaults:

- controller backend and protocol;
- connection defaults;
- work envelope and photography position;
- homing and post-job behavior;
- travel and work-feed ceilings;
- descriptive manufacturer, model, and capability metadata.

Profile defaults always have `allow_motion = false`, even when a profile object
is constructed from a value that requested motion.

### Tool-head profile

A `ToolHeadProfile` describes reusable laser defaults:

- controller power range and `M3`/`M4` mode;
- feed defaults;
- curve tolerance and guarded-boundary defaults;
- laser-spot offsets;
- optional nominal optical wattage and capability metadata.

Profile defaults always start with zero default power, zero frame power, and
low-power framing disabled. These are setup defaults, not permission to emit.

### Machine instance

A `MachineInstance` is one saved physical machine. It references a
machine profile and a tool-head profile but stores a complete validated snapshot
of its concrete `MachineSettings` and `LaserSettings`. This allows two machines
based on the same profile to retain different ports, work areas, speeds,
offsets, physical honeycomb spans, and calibration/camera bindings.

`MachineSettings.air_assist` is the machine-owned translation from the existing
binary `OperationLayer.air_assist` project setting to a trusted output. Its
constrained object contains `mode`, `fan_index`, `port`, and `baudrate`. Modes
are `disabled`; same-primary-controller GRBL coolant (`M8` / `M9`); same-primary
Marlin fan (`M106 P<n> S255` / `M107 P<n>`); and
`secondary_marlin_fan`. The secondary mode requires an explicit primary
protocol and gives the Pi one persistent Creality/Marlin serial owner; the
current E3 deployment keeps its separate primary explicitly GRBL. It uses fixed
`fan_index = 0` and only `M106 S255` / `M106 S0`: never a `P` parameter and
never `M107`. Its identified endpoint is
`/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0` at 115200 baud. Windows stores
that Pi-local path as opaque configuration and never opens it. There is no
percentage or arbitrary G-code field. E3 rejects `auto`, a mismatched or missing
mapping, an unknown mode, an invalid channel, or an invalid endpoint/baudrate
instead of guessing hardware.

`MachineSettings.honeycomb_span_mm` is optional machine-specific physical setup
data. `null` means not configured; a configured value must be a finite positive
number. Built-in profiles, including Creality Ender-3 S1 Pro, leave it `null`.
Selecting a profile or explicitly applying generic profile defaults never
invents a nominal span. Applying those defaults preserves an existing measured
span while replacing the generic controller, work-area, homing, and motion
values. The low-level registry duplicate is a complete detached snapshot, while
the Machine Manager duplicate action deliberately clears the camera,
calibration, and honeycomb-span bindings before saving the new instance.

The complete machine/head pair is validated with the same configuration rules
as the existing application. For example, a saved laser feed cannot exceed its
machine ceiling, invalid work areas and guarded polygons are rejected, and
boolean safety fields are not coerced from strings or numbers.

### Resolved machine configuration

`MachineRegistry.resolve_machine()` returns detached copies of the selected
instance, profile metadata, machine settings, laser settings, and optional
camera/calibration references. Mutating that returned object cannot mutate the
saved registry. `CoreRuntime` converts those values into detached running-machine
identity passed to `AppContext`: machine ID/name, machine/tool-head profile IDs,
and the expected camera/calibration profile IDs. `AppContext` does not retain a
mutable registry object and remains independent of Qt.

## Persistence and migration

The registry schema is explicitly versioned. Version 1 contains:

```json
{
  "schema_version": 1,
  "active_machine_id": "existing-machine",
  "machines": []
}
```

The actual `machines` array contains complete machine instances. JSON is parsed
strictly: duplicate keys, nonstandard numeric constants, unknown top-level or
machine fields, unsupported schema versions, unknown profile references, and
invalid machine/laser settings are rejected rather than silently repaired.

Initial migration uses no-clobber atomic publication. If another process creates
the registry first, E3 reads and validates that file instead of overwriting it.
Subsequent changes use the repository's existing atomic JSON replacement helper.

Fixture-reach observations used by the implemented read-only Coordinate Audit
are a separate diagnostic persistence domain:

```text
<data_dir>/machine_state/<stable-machine-id>/fixture_reach.json
```

The stable ID, not the editable machine name, selects the path. Renaming keeps
the evidence; duplicating creates a new ID and does not copy evidence. Direct
standalone `AppContext` construction uses an explicit `standalone` legacy scope.
Automatic machine-ID allocation also treats every existing
`machine_state/<id>` scope as permanently unavailable, including after its saved
machine is deleted. The retained directory acts as an evidence tombstone; E3
does not delete it, reuse the ID, or attach it to a newly created or duplicated
machine. An explicitly requested ID with an orphaned scope is rejected.

If the former global `<data_dir>/fixture_reach.json` exists, only the physical
saved machine whose detached provenance is `created_from: legacy-config` can
claim it. A profile-created or duplicated machine cannot claim it merely by
launching first. E3 validates the legacy evidence, atomically records the
claiming stable ID and source digest in
`machine_state/.fixture_reach_legacy_claim.json`, and copies the bytes only when
the machine-specific destination is absent. It never deletes or rewrites the
legacy source. Another ID cannot consume the same claim, and malformed evidence
or migration metadata blocks migration without guessing or overwriting data.

Migration deliberately makes only conservative classifications:

- legacy simulator configurations are rejected or retired by the dedicated
  removal migration and are never converted into physical identity;
- explicit GRBL configurations use the Generic GRBL profile;
- explicit Marlin configurations use the Generic Marlin profile;
- serial configurations using protocol auto remain Custom Machine;
- physical laser wattage is never inferred, so migrated hardware uses Custom
  Laser Head.

If legacy simulator state is active or is the only saved state, desktop startup
uses a separate read-only recovery inspection before credentials or runtime
construction. The recovery UI starts without a selected machine and requires an
explicit configured physical choice or creation of a new safe physical snapshot.
Finish preserves raw validated physical records, retires simulator records with
an exact backup, and transactionally rolls back configuration, registry,
credential, backup, and completion marker on failure. Cancel writes nothing.
Inactive simulator entries beside an active physical machine continue to be
retired automatically by the normal loader. Browser startup does not run a GUI
recovery flow; both the legacy simulation flag and simulator backend fail closed
with setup instructions. Normal packaged fallback recovery writes a durable user
configuration; an explicit `--config` is repaired in place.

The migrated instance preserves the current validated settings exactly,
including an existing operator-enabled `allow_motion` value or configured
nonzero default power. `CoreRuntime` resolves the selected instance once at
startup, but this preserves existing behavior rather than granting new
authority: the normal hardware, motion, coordinate, preflight, arming, and
program gates still apply.

Configurations and saved-machine records created before Air Assist support load
with the deterministic `disabled` mapping, fan index 0, and no active secondary
endpoint. The complete persisted mapping is the typed
`{mode, fan_index, port, baudrate}` object. Existing projects retain their
serialized per-layer Boolean; no project-schema migration or second Air Assist
field is introduced. Every built-in starting profile remains disabled.

## Built-in starting profiles

Machine profiles:

- Generic GRBL Laser
- Generic Marlin Laser
- Creality Ender-3 S1 Pro
- Custom Machine

Tool-head profiles:

- Generic 10 W Diode Laser
- Custom Laser Head

These profiles are starting points, not verified declarations about an attached
machine. A profile-derived physical machine still requires an explicit
controller port, reviewed limits, calibration, normal hardware-capable startup, motion
gate, homing/reference establishment, exact Preview, and ordinary temporary
laser arming before output.

## Safety boundary

`MachineService` remains the only normal path to the primary laser/motion
controller. `secondary_marlin_fan` adds only a Pi-local auxiliary path owned by
one persistent `CrealityControllerOwner`; Windows never opens it and it never
becomes a motion/laser path. That same owner must later be shared with the
separate S1 Z-homing/CR Touch work. All existing primary STOP, coordinate trust,
bounds, preflight, motion, and arming behavior remains in place.

When a powered output layer requests Air Assist, exact generation requires a
usable mapping. Structured preflight blocks a missing mapping whenever it can
prove powered serialized motion; ambiguous curved or bounded-work cases defer
to the same fail-closed exact generator. Preview and START cannot silently omit
the request or guess a command. Secondary jobs store exact strict non-comment
`E3AIRASSIST <mapping-sha256> ON|OFF` instructions in the canonical immutable
program bytes. A mapping or schedule change changes the finalized program
digest. The Pi validates and intercepts those instructions before the primary
stream; they never reach the primary controller. The resulting program is not
portable controller G-code and must not be submitted outside E3-aware execution.

The Pi owns execution after START. It checks every secondary ACK/timeout;
secondary failure fails the job, while primary `M5`/STOP remains authoritative.
Windows detach causes no fan transition. STOP acts on primary first and then
attempts bounded independent secondary OFF. Pi restart marks a running job
interrupted, never resumes it, and attempts acknowledged OFF. These actions are
not safety-rated and cannot guarantee delivery after controller, process,
network-node, serial, or power failure.

Creating or selecting a saved machine performs no hardware action. New
profile-derived instances begin without motion permission or positive laser
power defaults. The one migrated instance is a faithful record of existing
configuration, not a safety reset and not proof that the recorded values match
physical hardware.

## Deferred work

Machine Manager can create, edit, duplicate, and choose the next-launch
instance while the current process continues to use one immutable running
machine. Runtime hot-swapping is deliberately not implemented. If added, it
would require an explicit guarded lifecycle that blocks active jobs, issues
laser-off cleanup, disconnects the old controller, clears arming and coordinate
trust, swaps camera/calibration binding, invalidates prepared output, and
requires a fresh connection and Home before motion.

LightBurn `.lbdev` device-profile import remains a possible setup feature;
`.lbrn`/`.lbrn2` project import remains separate and machine-neutral.

## Historical verification for the registry increment

Focused portable tests cover exact legacy migration, conservative profile
classification, retired-simulator migration, safe profile-derived defaults, multiple
saved machines, active selection, update and reload, detached resolved values,
duplicate and unknown JSON rejection, future-schema rejection, invalid
machine/head pair rejection, guarded-polygon validation, removal rules,
no-clobber migration races, runtime settings identity, and fail-closed desktop
construction for an invalid registry. No physical controller, camera, motion,
or laser test is claimed for this data-model-only increment.


## Machine Manager and household deployment

The desktop Machine Manager exposes the registry through the normal E3 interface.
The existing validated E3 configuration is migrated automatically and appears as
the current running machine; opening the manager does not replace or reset its
controller, work area, laser settings, camera endpoint, focus, or calibration
profile.

The manager can add, edit, duplicate, delete, and select saved machines. Selecting
a machine changes the default for the next E3 launch. It deliberately does not
hot-swap the controller underneath an open job or running session.

Under **Work area and motion**, **Physical honeycomb ruler span** accepts an
explicit positive millimetre value or a blank **Not configured** state. Profile
identity changes and applying built-in machine profile defaults leave an
existing measured value untouched; no profile supplies 191 mm or any other
inferred physical measurement. The operator can still clear it explicitly and
save the machine.

Under the controller connection settings, **Air assist output** selects
**Disabled**, **GRBL coolant (M8/M9)**, **Marlin fan**, or the Pi-owned
**Creality / Marlin auxiliary fan**. Same-primary Marlin mode exposes a bounded
fan/channel index. The auxiliary choice fixes index 0 and exposes its Pi-local
endpoint and baudrate; those values are persisted on Windows but are not opened
there. Machine Manager applies the selection on a later launch and does not
hot-swap the immutable current runtime or contact either controller. The
ordinary Cuts / Layers editor remains machine-neutral and shows only the
per-operation **Air assist** checkbox.

`BUILD_E3_HOME_INSTALLER.bat` builds a private preconfigured installer from the
current Windows E3 user-state directory. The private installer seeds:

- `network-local.json`;
- `machines.json`;
- the active optical calibration profile;
- the household bridge credential.

Seed files use `onlyifdoesntexist`, so installing an update does not overwrite
machine state already configured on that Windows account. The home installer
contains a credential and must not be published as a public release artifact.
