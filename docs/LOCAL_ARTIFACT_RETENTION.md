# Local build storage and artifact retention

Every normal packaging entrypoint now calls `build_storage_guard.py` before
dependency installation or generated-output writes: the Windows installer,
private home installer, DEV TEST launcher and Linux AppImage. Keep using the
existing commands and parameters. The Windows outputs remain `dist/E3` and
`installer-dist/<OutputBaseFilename>.exe`; the Linux output remains
`E3-x86_64.AppImage`. A separate wrapper is not required for storage protection.

## Default build admission

The guard shares state across E3 worktrees. It measures the finite generated
output locations in discovered Git worktrees and known retained worktree roots,
including older unregistered builds. It records initial legacy usage as a
baseline and permits at most **eight GiB of additional growth**, with a two-GiB
reservation for each admitted build and a two-GiB free-space floor. This is a
growth cap, not a claim that existing artifacts occupy only eight GiB.

After reviewed cleanup lowers measured usage, the ceiling can ratchet downward
when no build leases are active. It never rises automatically to excuse growth.
Reservations are admitted atomically, so participating worktrees share the same
allowance. An unexpectedly large build may exceed its reservation; completion
records its real size and subsequent builds stop until storage is reviewed.
This is admission accounting, not a filesystem quota.

On Windows the state defaults to `%LOCALAPPDATA%/E3 Build Storage/state.json`.
Do not create fresh state to bypass a refusal. The guard measures generated
outputs, not unrelated personal folders. It does not prune Git history or
remove source, projects, profiles, calibration or evidence. Inaccessible
accounting fails closed.

## Ordinary rebuilds

An explicit normal build may replace its own previous successful generated
outputs in the same checkout. No fresh checkout or new command is required for
that ordinary case. The guard requires prior ownership, unchanged output
identities and contents, and fresh launcher/process/pin checks. It checks before
admission and again immediately before replacement, after dependency work.

Selected, running or rollback-pinned bundles are protected as whole output
trees, including `_internal` support files. Use a separate lightweight checkout
when testing a new build while one of those bundles is active. Unknown legacy
outputs and changed files are refused rather than silently adopted. Review
those paths separately; do not remove state or user files to bypass protection.
The Windows builder replaces only its exact named installer and preserves other
files in `installer-dist`.

An interrupted or failed build retains its lease and reservation for review.
Only successful completion records new ownership and releases the reservation.
The guard does not add background pruning: replacement is limited to the known
outputs of the build explicitly invoked. Older worktree builds remain present
until a separate exact cleanup manifest is approved. Older checkout scripts do
not participate in leases; never run them concurrently in the same checkout.

## Private home installers

`BUILD_E3_HOME_INSTALLER.bat` still calls `build_home_installer.ps1`, which uses
the guarded Windows builder with `-MachineSeed` and the home channel. Private
outputs contribute to capacity accounting using filesystem metadata only.
Neither credentials nor secret payload hashes go to the guard or a prunable
artifact registry.

An explicit private rebuild may replace only its own prior successful,
unchanged, metadata-owned outputs. Unknown private installers or seed folders
need separate review. Public builds cannot silently consume private seed
residue. Original household configuration and calibration stay in their normal
per-user locations.

## Hosted CI

The publication workflow explicitly passes `-EphemeralCI` on Windows and
`--ephemeral-ci` to the Linux script. The guard verifies hosted-runner and
workspace evidence; a flag or `CI=true` alone is not an unrestricted bypass.
Ephemeral jobs require fresh output paths and have bounded capacity checks
instead of a personal DEV TEST pointer. Uploaded intermediate packages expire
after seven days. Published release assets are unchanged by this rule.

## Read-only cleanup planning

`artifact_retention.py` remains a separate explicit registry of completed public
Windows bundles, individual installers and PyInstaller scratch. It produces a
dry-run cleanup plan and has **no deletion executor**; `--apply` refuses. A plan
is review evidence, not authorization to delete.

Use the registry alongside shared state so normal builds consult its pins:

```powershell
python .\packaging\artifact_retention.py plan `
  --registry "$env:LOCALAPPDATA\E3 Build Storage\retention-registry.json" `
  --launcher-pointer 'C:\Users\lukel\Documents\E3 Dev Test\current-feature.json'
```

The cleanup proposal preserves selected/running outputs, unresolved leases,
explicit keep pins, an explicitly verified rollback, the newest two otherwise
unprotected builds, and builds younger than seven days. Its eight-GiB registry
target is informational and never overrides protection. That target differs
from the normal builder's baseline-plus-eight-GiB growth cap. Missing rollback
evidence blocks cleanup candidates.

Registration validates allowed paths, packaged metadata and complete content
fingerprints. Modified/missing payloads, hardlinks, reparse points, ancestor
escapes, unverified launchers and incomplete process inspection block approval.
A whole worktree is never treated as scratch.

After identifying a registered build as known-good, pin its exact ID:

```powershell
python .\packaging\artifact_retention.py pin `
  --registry "$env:LOCALAPPDATA\E3 Build Storage\retention-registry.json" `
  --build-id 'verified-build-id' --role rollback
```

Use `--role keep` for an exceptional build. A pin is not physical validation;
retain test evidence separately. Existing alternate registries can be supplied
with `build_windows.ps1 -RetentionRegistry <path>`; the guard remembers them.
Review exact paths, fingerprints, logical sizes and protections immediately
before separately approved cleanup. Logical bytes are not guaranteed recoverable
capacity; moving files to Recycle Bin does not free that capacity.

## Optional strict wrapper

`build_with_retention.ps1` defaults to inventory only. Its explicit `-Build`
mode is a stricter fresh-output workflow with public-build registration and also
invokes the mandatory guard. It is useful for a new frozen feature checkout;
ordinary rebuilds can keep using `build_windows.ps1`. Its `-Python` selects the
registry interpreter, while the builder retains checkout-venv/Python selection.
Its optional eight-GiB registry admission budget cannot bypass the mandatory
shared growth cap.

## Source duplication and verification limits

The October 2026 audit also found tracked `.codex-worktrees` source snapshots
and calibration data. Git reproduces tracked content even when ignore rules
mention the directory. Artifact guards do not remove those source files;
source-tree cleanup needs its own review preserving originals, unique work,
calibration and history.

Tests use temporary synthetic bundles, manifests, links and stub build tools.
They do not build E3, change launcher selection or connect hardware. This source
change does not reclaim existing space by itself. Deployment into active
checkouts and approved one-time cleanup must be recorded separately. No
automatic destructive retention policy is activated.
