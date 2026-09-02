# Permanent Windows feature-test launcher

`E3 DEV TEST` is the stable entry point for operator testing of frozen Windows
feature builds. It is intentionally distinct from the installed production E3
application and does not change project data, configuration, machine authority,
motion, arming, laser output, STOP, or any other hardware behavior.

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

## Runtime identity

The launcher supplies:

```text
E3_DEV_TEST=1
E3_DEV_TEST_FEATURE=<pointer name>
E3_DEV_TEST_VERSION=<pointer version>
E3_DEV_TEST_BRANCH=<pointer branch>
E3_DEV_TEST_REVISION=<pointer revision>
```

E3 consumes the first three only for visible/build identity. In that explicit
mode its title begins `E3 DEV TEST — <feature> — v<version>`, it uses the orange
DEV window icon, and it assigns `E3.DevTest` before Qt creates any UI. Without
the exact activation value `E3_DEV_TEST=1`, the existing production title, icon,
and implicit AppUserModelID path are unchanged. QSettings organization/name,
user data, project schemas, configuration loading, and machine behavior do not
change in either mode.

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
  -PointerSource '.\path\to\validated-current-feature.json'
```

The installer script copies the one-file launcher and pointer to the permanent
Documents location and recreates only `Desktop\E3 DEV TEST.lnk`. It does not
create or alter a taskbar pin and does not touch production E3.

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
