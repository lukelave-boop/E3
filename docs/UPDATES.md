# Desktop packaging and automatic updates

E3 separates replaceable application files from persistent machine/user state.

Release checks require the Windows installer product version, Windows and Linux
bundle metadata, and update manifest to agree on version and exact revision.
The package validation workflows build and inspect artifacts without starting
E3. Publication remains gated on the full compatibility workflow.
The Windows PE reader removes only Inno Setup's documented trailing ASCII-space
padding and records the raw resource value in CI. Other whitespace and all
JSON identity fields remain subject to the exact comparison.

## Persistent state

The native per-user root holds initial machine state and version directories:

- Windows: `%LOCALAPPDATA%\E3 Positioning System\`
- Linux: the configured XDG data directory, or the platform user-data fallback.

Existing pre-isolation installations continue to read their original root.
New desktop builds select independent version state before loading settings.

The private machine-seeded Windows installer writes initial files only when they
do not already exist:

- `config/network-local.json`
- `data/calibration_profiles/...`
- `secrets/bridge-token.txt`

Inno Setup marks them `onlyifdoesntexist` and `uninsneveruninstall`. Updates
therefore replace application files only. Existing configuration, credentials,
calibration, templates, materials, projects, and captures are not overwritten.

## Independent version state

Automatic Windows and Linux update handoffs clear the current process's E3
state, config, credential and build overrides and preserve the genuine user
profile. The updated application then selects its own installed-version scope.
This also clears caller-supplied per-launch E3 overrides for that automatic
handoff; explicitly launching a build with overrides remains supported.

An installed desktop build uses
`state-scopes/installed/<version>-<revision>/` beneath the native per-user root.
Its first launch creates a complete private snapshot; its later launches reuse
that same snapshot. Configuration, saved machines, calibration, credentials,
materials, templates, autosaves and new Qt preference files are kept in that
version's state. A newer build cannot rewrite an older version's copy through
the normal desktop launch path.

After the main window has been constructed successfully, the installed build
records `state-scopes/installed/last-successful.json`. A strictly newer numeric
version can use that successful version's state as its first-launch source.
Reopening an older version uses its existing copy and does not move the pointer
backward. First launches of older or unorderable versions use the original
legacy seed rather than importing a newer schema. Same-version revisions do
not import one another automatically. State is copied before any migration;
the prior version remains intact if the new copy cannot load.

Snapshot preparation copies calibration evidence and machine values exactly,
rebases only the copied `app.data_dir`, includes configuration sidecars and
external configured data directories, and uses SQLite's backup operation.
It rejects linked paths, incomplete destinations and changes to ordinary source
files during the copy. An import error stops startup with a configuration error;
there is no writable fallback to the source. Downloads and nested version copies
are excluded. Source desktop checkouts receive a separate scope per checkout.

The permanent [E3 DEV TEST launcher](DEV_TEST_LAUNCHER.md#independent-test-state)
also isolates existing frozen test executables before they start. These builds
use `state/<version>-<revision>/local/E3 Positioning System/` beside the permanent
launcher in `Documents/E3 Dev Test`. New source moves window and trace preferences
into private INI files, importing old Qt preferences once
without writing the original registry. Older frozen executables retain their
shared Qt UI-preference keys; their machine and calibration files are isolated
by the launcher. Each test launch supplies private build metadata whose version
and revision still identify the selected executable but whose update-manifest
URL is empty. This disables installation from the test application's existing
updater, including older frozen builds, without modifying the executable or its
adjacent metadata. Application updates remain available through regular E3;
test selection remains the permanent launcher's responsibility.

The launcher's separate `state/last-prepared-test.json` carries test settings
forward to a strictly newer numeric test version. It is published after the
snapshot is complete, before the child starts; it does not claim a successful
app or hardware test. Exact existing test scopes always win. An unseen older,
equal-version/different-revision or unorderable test build starts from the regular
seed rather than importing newer settings. Copy and pointer publication are
serialized, and reopening an older scope cannot move the pointer backward.

Explicit `--config` or `E3_CONFIG_PATH` invocations intentionally retain that
configuration and its selected machine-data directory; their auxiliary defaults
and new Qt preferences are scoped separately. `E3_USER_STATE_DIR` remains an
explicit caller-selected root. These overrides are for deliberate deployments,
not the ordinary installed or DEV TEST launch path.

### Recovering an affected older installation

An already installed binary is not rewritten to accept unfamiliar settings.
First preserve the complete newer state in its own version directory and a
recovery backup. Then validate the older application's known compatible files
with its own parsers and restore only the incompatible files. For the reported
0.7.153 failure, the available configuration and machine-registry backups differ
only by the added `machine.raster_travel_envelope` fields; current lens, bed and
honeycomb calibration already load in that version. Expanded raster-travel
evidence belongs to the newer build's preserved state. That recovery was applied
and the original installed 0.7.153 executable opened interactively with supported
machine values and calibration preserved. The full pre-repair state backup is
under the permanent launcher's `recovery-backups/` directory.
See [current state](../CURRENT_STATE.md) for exact identities and verification;
this procedure is not a general schema downgrade.

Local editor startup does not require a successful Pi connection. Missing or
incompatible Pi services should report an offline/error state for the affected
camera or controller functions while local project editing remains available.
Protocol checks and hardware authorization remain enforced; no automatic protocol
fallback or hardware update is introduced by state isolation.

Project documents are a separate persistence domain. A file deliberately opened
and saved by a newer version retains that version's schema; keep an older-format
copy when it must also open in an older application. Version state isolation
does not convert shared project documents.

## Update channel

The update offer shows the exact package size from the manifest. During the
download, a determinate bar shows percentage alongside downloaded/total bytes
and current speed (decimal KB/MB/GB per second, averaged over roughly three
seconds). The display refreshes four times per second and the speed falls to
zero if data stops arriving. At 100%, the dialog explicitly reports verification
and saving; installation is offered only after size and SHA-256 checks pass.

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
After unsaved-project close approval, E3 creates the verified installer process
before committing shutdown or arming its four-second forced-exit timer. A slow
Windows process launch therefore cannot be killed by the teardown deadline.
If creation fails, E3 remains open and reports the error. Successful creation
then enters the ordinary bounded shutdown; worker cleanup cannot delay it
indefinitely. The cache retains `<installer>.launch.json` with the latest launch
stage, elapsed time, process identity or error, and `<installer>.setup.log` with
Inno Setup diagnostics. These local files do not contain the child environment.
If process creation fails after E3's final close, a standalone error shows the
verified installer path for manual launch and E3 exits; the stopped desktop is
not re-shown as if it were usable. This fallback is for windows without the
production pre-shutdown hook. This boundary is automated-test covered; a
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

Before either package is built, the publisher calls the complete Compatibility
CI workflow from the exact same commit. This includes Windows Python 3.10 core,
Windows Python 3.12 desktop, Ruff, and POSIX controller/session tests. Failed,
cancelled or skipped compatibility jobs block packaging and publication on both
push and manual runs. A green run on an earlier branch commit is insufficient.
Only the publication job has repository write permission. The separate main/PR
CI remains available for review; it is not used as a stale external status token.

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
to the current `main` revision. Previous package assets are retained temporarily
so a client that already fetched the prior manifest can finish its verified download.
Old manifest-backup cleanup is best effort after publication; cleanup failure is
reported without rolling back the working new update.

### Package retention

Each successful publication keeps the current Windows/Linux package pair and
the two newest other complete pairs, ranked by their original upload time.
Older pairs, including the legacy unversioned packages, first receive a dated
retirement label. They can be deleted by a subsequent successful publication
only after both members have spent at least seven days retired. The first run
starts this grace period for existing old assets; it does not immediately remove
the accumulated downloads. This is a minimum retention period, not a strict
asset-count limit or a daily cleanup schedule.

Labels retain the filename and record the retirement time in GitHub metadata;
download URLs and file contents do not change. Becoming current or one of the
recent pairs clears the retirement markers. Re-publishing an old revision clears
its markers **before** promoting its manifest, so even interrupted publication
cannot reuse an expired grace period for a newly active download.

Only recognized E3 package names are eligible. Unknown files, custom labels,
invalid metadata and incomplete unmarked pairs are preserved. An interrupted
deletion can finish removing an already retired pair's remaining member. The
publisher rechecks the authoritative manifest and candidate asset before every
cleanup operation, defers cleanup while a manifest recovery backup remains, and
reports cleanup failure as a warning without rolling back the working update.
The existing workflow concurrency group serializes publication and cleanup;
do not run independent publishers concurrently against this release.

The live manifest, release and tag remain intact. GitHub's automatic source
archives are unaffected. An update dialog left open beyond the grace period may
need a fresh update check if its old package has since been removed.

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
