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

The development workflow publishes fixed prerelease tag `e3-development` when
`fix/live-monitor-display-throughput` changes. It contains:

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
