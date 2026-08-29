# Desktop packaging and automatic updates

E3 separates replaceable application files from persistent machine/user state.

## Persistent state

Packaged E3 reads machine configuration and the bridge credential from:

- Windows: `%LOCALAPPDATA%\E3 Positioning System\`
- Linux: `~/.local/share/e3-positioning-system/`

The private machine-seeded Windows installer writes initial files only when they
do not already exist:

- `config/network-local.json`
- `data/calibration_profiles/...`
- `secrets/bridge-token.txt`

Inno Setup marks them `onlyifdoesntexist` and `uninsneveruninstall`. Updates
therefore replace application files only. Existing configuration, credentials,
calibration, templates, materials, projects, and captures are not overwritten.

## Update channel

Updater-enabled builds contain `build-info.json`, including the Git revision,
channel, and manifest URL. **Help > Check for Updates…** downloads the channel
manifest, selects the current platform, and verifies exact size and SHA-256
before launching the package.

On Windows, the verified Inno Setup executable is an external program rather
than part of the frozen E3 bundle. Immediately around process creation, E3
temporarily restores the standard Win32 DLL search directory and gives the
child a copied environment with only PyInstaller-bundle-rooted `PATH` entries
removed. The installer is started detached, with its parent directory as the
working directory, before E3 exits. E3 restores its own DLL search state if
process creation fails. Once Windows creates the installer process, that child
is authoritative even if the exiting E3 parent cannot restore its own DLL state.
If process creation fails after E3's final close, a standalone error shows the
verified installer path for manual launch and E3 exits; the stopped desktop is
not re-shown as if it were usable. This boundary is automated-test covered; a
newly built installed package must still be exercised before calling the
handoff package-verified. That exercise is intentionally deferred until a
disposable interactive Windows environment is available; it must not repoint or
replace the public development channel or overwrite the developer's installed
E3 application.

The development workflow publishes fixed prerelease tag `e3-development` when
the canonical `main` branch changes. Manual publication is also restricted to
`main`, so the in-app updater cannot be repointed at a feature branch. It contains:

- `E3-Setup.exe`
- `E3-x86_64.AppImage`
- `update-manifest.json`

## Private initial installer

Public update packages contain no Pi bridge secret and no machine calibration.
Build the initial private installer locally:

```powershell
$env:E3_BRIDGE_TOKEN = '<machine bridge token>'
.\packaging\build_windows.ps1 -MachineSeed
```

The result is `installer-dist\E3-Setup.exe`. Keep it private because it seeds
the local bridge credential.

## Generic update packages

Windows:

```powershell
.\packaging\build_windows.ps1
```

Linux:

```bash
./packaging/build_linux_appimage.sh
```
