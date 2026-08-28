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
before launching the package. Manifest requests use four bounded attempts for
transient HTTP 404, 408, 429, and 5xx responses, with 0.5, 1, and 2 second
delays. Malformed URLs, malformed manifests, channel/revision mismatches, and
package size or SHA-256 failures retain their normal immediate rejection; only
manifest retrieval receives the transient HTTP retry.

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
the canonical `main` branch changes application source, packaging, or runtime
dependencies. Pushes that change only documentation, tests, repository
instructions, issue/pull-request templates, or the normal CI workflows are
ignored. A mixed push that also changes a product-affecting file still publishes.
Manual publication is also restricted to `main`, so the in-app updater cannot
be repointed at a feature branch.

Each successful revision adds immutable package assets named:

- `E3-Setup-<12-character-revision>.exe`
- `E3-<12-character-revision>-x86_64.AppImage`

The updater continues to read the stable manifest URL:

`https://github.com/lukelave-boop/E3/releases/download/e3-development/update-manifest.json`

Publication never deletes the live release or tag. The publisher first uploads
both revision-specific packages and checks GitHub's uploaded state, exact size,
and server-reported SHA-256 against the local files. It then uploads and verifies
a revision-specific staged manifest whose package URLs, sizes, and hashes match
those assets. Any failure before that point leaves the active manifest and old
packages untouched.

GitHub release assets cannot be overwritten in place: the supported CLI
`--clobber` behavior deletes the old asset before uploading its replacement,
and the release-asset API can rename but cannot replace content. E3 therefore
uses a recoverable near-atomic name switch. It renames the old stable manifest
to a unique backup and immediately renames the already-uploaded staged manifest
to `update-manifest.json`. Cancellation signals are deferred across those two
metadata operations, and an `always()` recovery step restores the verified old
manifest if a run is interrupted between them. A client racing the very short
two-request name transition uses the bounded manifest retry above and receives
either the complete old revision or the complete new revision.

Only after the new stable manifest is verified does the workflow move the
`e3-development` tag and update the prerelease title, body, and target metadata
to the current `main` revision. Previous package assets are retained so a client
that already fetched the prior manifest can still finish its verified download.
Old manifest-backup cleanup is best effort after publication; cleanup failure is
reported without rolling back the working new update.

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
