# Machine profiles and saved machines

## Status

This document describes the first multi-machine foundation increment. It adds a
versioned saved-machine registry and reusable machine/tool-head profiles without
changing which configuration controls the running camera, controller, motion,
or laser.

At desktop startup, `CoreRuntime` creates or loads
`<data_dir>/machines.json`. When that file does not exist, E3 atomically records
one `existing-machine` entry containing an exact copy of the already validated
`machine` and `laser` sections from the active JSON configuration. Existing
hardware behavior therefore remains unchanged: the loaded `Settings` object is
still passed to `AppContext` and remains the only runtime authority.

The registry is intentionally not yet exposed as a Machine Manager, and
changing the saved active-machine ID does not reconnect, home, move, arm, or
switch the running controller. Runtime switching is a separate guarded
lifecycle change. A malformed registry is rejected before the desktop
constructs `AppContext`; E3 never guesses which saved machine the operator
intended.

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

A `MachineInstance` is one saved physical or simulated machine. It references a
machine profile and a tool-head profile but stores a complete validated snapshot
of its concrete `MachineSettings` and `LaserSettings`. This allows two machines
based on the same profile to retain different ports, work areas, speeds,
offsets, and future calibration/camera bindings.

The complete machine/head pair is validated with the same configuration rules
as the existing application. For example, a saved laser feed cannot exceed its
machine ceiling, invalid work areas and guarded polygons are rejected, and
boolean safety fields are not coerced from strings or numbers.

### Resolved machine configuration

`MachineRegistry.resolve_machine()` returns detached copies of the selected
instance, profile metadata, machine settings, laser settings, and optional
camera/calibration references. Mutating that returned object cannot mutate the
saved registry.

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

Migration deliberately makes only conservative classifications:

- simulator configurations use the simulator machine and simulated head;
- explicit GRBL configurations use the Generic GRBL profile;
- explicit Marlin configurations use the Generic Marlin profile;
- serial configurations using protocol auto remain Custom Machine;
- physical laser wattage is never inferred, so migrated hardware uses Custom
  Laser Head.

The migrated instance preserves the current validated settings exactly,
including an existing operator-enabled `allow_motion` value or configured
nonzero default power. This preserves current behavior; it does not grant new
authority because the registry is not yet connected to execution.

## Built-in starting profiles

Machine profiles:

- Simulator
- Generic GRBL Laser
- Generic Marlin Laser
- Creality Ender-3 S1 Pro
- Custom Machine

Tool-head profiles:

- Generic 10 W Diode Laser
- Custom Laser Head
- Simulated Laser Head

These profiles are starting points, not verified declarations about an attached
machine. A profile-derived physical machine still requires an explicit
controller port, reviewed limits, calibration, hardware-enabled startup, motion
gate, homing/reference establishment, exact Preview, and ordinary temporary
laser arming before output.

## Safety boundary

This increment does not add another controller path. `MachineService` remains
the only normal controller route, and all existing STOP, disconnect, coordinate
trust, bounds, preflight, motion, and arming behavior remains in place.

Creating or selecting a saved machine performs no hardware action. New
profile-derived instances begin without motion permission or positive laser
power defaults. The one migrated instance is a faithful record of existing
configuration, not a safety reset and not proof that the recorded values match
physical hardware.

## Next increments

The next guarded increment is a desktop Machine Manager that can create, edit,
duplicate, and select instances while the current runtime continues to use one
machine. Runtime machine switching must then be implemented as an explicit
lifecycle operation that blocks active jobs, issues laser-off cleanup,
disconnects the old controller, clears arming and coordinate trust, swaps the
camera/calibration binding, invalidates prepared output, and requires a fresh
connection and Home before motion.

After that foundation is verified, LightBurn `.lbdev` device-profile import can
populate machine setup, and `.lbrn`/`.lbrn2` project import can remain separate
and machine-neutral.

## Verification for this increment

Focused portable tests cover exact legacy migration, conservative profile
classification, simulator migration, safe profile-derived defaults, multiple
saved machines, active selection, update and reload, detached resolved values,
duplicate and unknown JSON rejection, future-schema rejection, invalid
machine/head pair rejection, guarded-polygon validation, removal rules,
no-clobber migration races, runtime settings identity, and fail-closed desktop
construction for an invalid registry. No physical controller, camera, motion,
or laser test is claimed for this data-model-only increment.
