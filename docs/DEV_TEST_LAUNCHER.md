# Permanent Windows feature-test launcher

`E3 DEV TEST` is the stable entry point for operator testing of frozen Windows
feature builds. It is distinct from the installed production E3 application.
Before starting the selected executable, it prepares an independent copy of
machine settings, calibration and other writable application data for that exact
version/revision. Machine authority, motion, arming, laser output and STOP retain
the selected build's existing guards.

## Permanent files

```text
C:\Users\lukel\Documents\E3 Dev Test\E3 DEV TEST.exe
C:\Users\lukel\Documents\E3 Dev Test\current-feature.json
Desktop\E3 DEV TEST.lnk
```

The executable is a windowed, one-file launcher with its own orange `DEV` icon
and explicit Windows AppUserModelID `E3.DevTest`. It reads the pointer beside
it, launches only the selected EXE, and waits invisibly for that child. It never
falls back to production E3. The Desktop shortcut targets the permanent EXE,
uses the same icon and AppUserModelID, and leaves the normal E3 shortcut alone.
Taskbar pinning remains a normal user action; do not automate it.

## Pointer contract

The pointer has exactly these five string fields:

```json
{
  "name": "Outer silhouette",
  "version": "0.6.161",
  "branch": "feature/trace-outer-silhouette",
  "revision": "<exact build Git revision>",
  "exe": "C:\\absolute\\path\\to\\dist\\E3\\E3.exe"
}
```

The launcher bounds the UTF-8 JSON and every field, rejects duplicate, missing,
or unknown fields, requires a sane version/branch/hexadecimal revision, and
accepts only an existing absolute local-drive `.exe`. It also requires adjacent
schema-1 Windows x86-64 `build-info.json` metadata whose packaged flag, version,
and revision match the pointer. Invalid configuration produces a native Windows
error dialog headed `E3 DEV TEST — Launch failed`; production E3 is never used
as a fallback.

## Selecting a feature build

Build the feature from its exact checked-out revision. If a requested test
version is intentionally frozen, use the repository's documented
`E3_BUILD_VERSION` override while keeping the exact new Git revision in
`build-info.json`. Then update the pointer atomically:

```powershell
.\.venv\Scripts\python.exe .\packaging\set_dev_test_feature.py `
  --output 'C:\Users\lukel\Documents\E3 Dev Test\current-feature.json' `
  --name 'Feature name' `
  --version '0.6.000' `
  --branch 'feature/example' `
  --revision '<exact frozen build revision>' `
  --exe 'C:\absolute\feature-worktree\dist\E3\E3.exe'
```

Changing this JSON is the only normal launcher update. The pinned executable
and Desktop shortcut remain stable.

Feature builds must be produced with `packaging/build_windows.ps1`. The build
now checks shared storage capacity and protects selected, running and pinned
output trees before replacing its own generated files. Keep using the same
command. If legacy or protected outputs already occupy the checkout, the build
uses a fresh `.e3-builds/<mode>-<build-id>` output directory and prints its exact
paths. Select that new build's `dist/E3/E3.exe` explicitly; the build itself does
not change the launcher pointer. See [local build storage](LOCAL_ARTIFACT_RETENTION.md)
for admission checks and the separate dry-run cleanup inventory. The build
sanitizes foreign ICU/OpenSSL directories from the PyInstaller environment and
rejects known conflicting root-level native libraries before a build can be
selected.
If the selected frozen application encounters an import failure before Qt is
available, it records the original exception and runtime context in the
per-user `logs/startup-error.log` and displays a native Windows error dialog.
That diagnostic is part of the selected E3 build, not the permanent launcher.

## Independent test state

The launcher stores each test build's state beside the permanent launcher:

```text
<launcher directory>\state\<version>-<revision>\local\E3 Positioning System\
```

For the permanent installation, the launcher directory is
`C:\Users\lukel\Documents\E3 Dev Test`. These writable state directories are
separate from the selected frozen application bundle and from regular E3's
LocalAppData state.

The first test scope starts from the regular application's existing state.
A strictly newer numeric test version can copy the latest prepared test scope,
carrying forward settings and calibration saved during testing. Later launches
of an exact version/revision reuse its own copy, even after another build has run.
Selecting another version never refreshes or overwrites an existing copy.

`state/last-prepared-test.json` records that import source after a complete
snapshot has been prepared, before the child application starts. It records
preparation only, not successful application startup or hardware qualification.
An unseen older version, another revision with the same version, or an
unorderable version uses the regular-app seed rather than importing newer state.
The launcher never strips unknown settings or performs a schema downgrade.
Existing exact scopes can open independently of a damaged or busy import pointer.
A failed import stops the launch; it never falls back to writing regular state.

The copy includes machine profiles, camera/bed calibration, configuration
sidecars, credentials, material recipes, templates and local recovery data.
SQLite databases use a consistent backup. Only copied configuration data-directory
paths are rebased; calibration evidence, machine values and safety limits stay
unchanged. Download caches and other version copies are excluded. A
`state-scope.json` manifest records the source and rebased data locations.

The launcher supplies `E3_USER_STATE_DIR`, `E3_CONFIG_PATH` and a scoped
credential-file path. It also redirects the child process's profile directories
so older frozen builds' material, autosave and legacy file paths stay private.
This works without rebuilding the selected E3 executable. Test launches also
receive private build metadata with the original version/revision and an empty
update-manifest URL. The existing updater therefore stays disabled, including
in older frozen builds. Install application updates from regular E3; select test
builds through the permanent launcher. The original executable and adjacent
`build-info.json` are unchanged.

An older build still requires settings it understands; the launcher does not discard unknown safety
settings or downgrade a newer schema. Recovery of an already affected build
uses a verified compatible backup after preserving the newer state separately.

New E3 source stores Qt preferences in its own version directory. Older frozen
executables still share their original Windows Qt registry keys for window and
trace preferences; those UI preferences are separate from the isolated machine
settings and calibration files. User-selected `.e3laser` files remain explicit
shared documents and retain normal schema compatibility checks.

## Runtime identity

The launcher supplies:

```text
E3_DEV_TEST=1
E3_DEV_TEST_FEATURE=<pointer name>
E3_DEV_TEST_VERSION=<pointer version>
E3_DEV_TEST_BRANCH=<pointer branch>
E3_DEV_TEST_REVISION=<pointer revision>
```

The title begins `E3 DEV TEST — <feature> — v<version>`, the window uses the
orange DEV icon, and E3 assigns `E3.DevTest` before Qt creates any UI. These
identity values are separate from the private state paths described above.
Without `E3_DEV_TEST=1`, the production title, icon and implicit AppUserModelID
remain unchanged. State isolation does not change project schemas or machine
execution behavior.

The launcher also resets PyInstaller's child-process environment, removes only
its own temporary bundle-rooted `PATH` entries, and temporarily restores normal
Windows DLL lookup while it creates the separately frozen E3 process. It is a
Windows GUI executable and does not create a console window.

## Building or reinstalling the permanent launcher

The launcher normally does not need rebuilding. When its own implementation or
icon changes:

```powershell
.\packaging\build_dev_test_launcher.ps1
.\packaging\install_dev_test_launcher.ps1 `
  -LauncherSource '<exact launcher EXE path printed by the build>' `
  -PointerSource '.\path\to\validated-current-feature.json'
```

The launcher build also uses a fresh managed output directory when its old
outputs need preservation. Pass the printed path explicitly so installation
cannot select an older launcher from `launcher-dist`.

The installer script copies the one-file launcher and pointer to the permanent
Documents location and recreates only `Desktop\E3 DEV TEST.lnk`. It does not
create or alter a taskbar pin and does not touch production E3.

The 2026-10-16 state-isolation repair updated only the permanent launcher and
retained its prior executable under `launcher-backups/`. It did not rebuild the
selected feature executable or the installed regular application. That launcher
passed a frozen-process isolation probe; the unchanged regular 0.7.153 also
opened interactively after its compatible settings were restored. Exact hashes,
backup locations and remaining verification are in [current state](../CURRENT_STATE.md).

## Feature-build report

Every feature-build handoff that requires Windows operator testing begins:

```text
TEST THIS BUILD:
E3 DEV TEST

Feature: <name>
Version: <version>
Branch: <branch>
Revision: <exact build revision>
Target EXE: <absolute frozen EXE path>
```
