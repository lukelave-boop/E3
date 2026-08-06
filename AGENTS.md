# Repository development instructions

## Start here

These instructions apply to the entire repository. Before changing code, read:

1. `CURRENT_STATE.md`
2. `SAFETY.md`
3. the relevant document under `docs/`
4. `git status --short`

Preserve unrelated and pre-existing working-tree changes. Do not normalize line
endings or rewrite files outside the task's scope.

## Safety invariants

This application controls hazardous laser and motion hardware. Maintain these
invariants:

- Simulation is the default.
- `MachineService` is the only normal path to a controller.
- Serial hardware requires an explicitly hardware-enabled process.
- Motion requires `machine.allow_motion`.
- Positive laser output requires temporary arming.
- Manual commands remain limited to read-only queries and `M5`.
- Streamed jobs remain bounded, absolute-millimetre programs using the guarded
  allowlist.
- Rapid travel must not occur while laser output is enabled.
- Programs must establish `G21`, `G90`, and an initial `M5`, and end with a
  standalone `M5`.
- Stop, disconnect, disarm, and job-failure paths must attempt `M5`.
- Software controls must never be described as safety-rated.
- Hardware behavior or configuration values are not verified until a physical
  test is recorded with the controller, firmware, configuration, and result.

Any change to motion, arming, laser output, parsing, bounds, or stop behavior
requires focused tests for both acceptance and rejection paths.

## Architecture boundaries

- Keep configuration, geometry, project, calibration, vision, and G-code
  modules independent of Qt and HTTP.
- `AppContext` owns the shared camera, calibration, vision, and machine
  services.
- `CoreRuntime` is the UI-neutral lifecycle wrapper used by the desktop.
- Browser behavior belongs in `server.py` and `web/`.
- Desktop behavior belongs in `desktop/`.
- Persistent project state belongs in `project/`; never put Qt objects in the
  `.e3laser` model.
- Platform-specific camera and serial implementations must be imported lazily
  through portable interfaces. Simulator mode must import and run on Windows
  and Linux.
- The browser's single-SVG pipeline and desktop project pipeline are currently
  distinct. Identify which pipeline a change affects and test both when shared
  behavior changes.
- A loaded project's work area and the configured machine work area must not
  silently disagree at execution time.

## Platform contract

The portable core and simulator are intended to run on Windows and Linux.
Linux-only hardware behavior must fail clearly and must not prevent portable
modules or simulator tests from importing on Windows.

Use platform-native paths for new user data. Do not add new hard-coded
`~/.local`, `/dev`, `.venv/bin`, or `.venv/Scripts` assumptions to portable
code. POSIX-only tests must skip explicitly on unsupported platforms rather
than failing during collection. Add equivalent Windows tests when a Windows
backend is introduced.

The current platform boundary and known blockers are recorded in
`CURRENT_STATE.md`.

## Development workflow

1. Inspect the working tree and `CURRENT_STATE.md`.
2. Make the smallest coherent change.
3. Add or update focused tests.
4. Run the platform-neutral suite on the active OS.
5. Run platform-specific tests where that platform is available.
6. Update `CURRENT_STATE.md` when verification, supported behavior, known gaps,
   platform status, or active working-tree work changes.
7. Update the README, architecture, roadmap, and changelog when user-visible
   behavior changes.

Do not commit captures, calibration photographs, logs, generated G-code, trace
previews, or local configuration unless a task explicitly makes them curated
fixtures.

## Verification commands

Linux:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check .
```

Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
```

Until the POSIX import boundary is fixed, the documented Windows diagnostic
subset is:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider `
  --ignore=tests/test_app_simulation.py `
  --ignore=tests/test_machine.py `
  --ignore=tests/test_serial_posix.py
```

Do not treat that subset as the final Windows success criterion.

For desktop changes, distinguish among:

- source parsing or static assertions;
- offscreen widget smoke tests;
- interactive GUI tests;
- real camera tests;
- real controller or laser tests.

State exactly which level was performed.

## Project and persistence compatibility

- Keep `.e3laser` schema changes explicit and versioned.
- Add migration or rejection tests for schema changes.
- Preserve atomic save, backup, and autosave behavior.
- Treat project files, calibration data, material databases, and Qt settings as
  separate persistence domains.
- Never silently drop unsupported object or layer types during toolpath
  generation; reject them with a useful message.

## Definition of done

A change is complete only when:

- relevant automated tests pass;
- supported Windows and Linux imports remain valid;
- safety invariants are preserved;
- generated or local artifacts are not accidentally included;
- user-facing documentation matches the implementation;
- `CURRENT_STATE.md` records what is tested, smoke-tested, implemented but
  unverified, historically verified, and physically verified.
