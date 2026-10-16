# Local build storage and artifact retention

Every normal packaging entrypoint now calls `build_storage_guard.py` before
dependency installation or generated-output writes: the Windows installer,
private home installer, DEV TEST launcher and Linux AppImage. Keep using the
existing commands and parameters. Windows builds use `dist/E3` and
`installer-dist/<OutputBaseFilename>.exe` when those locations can safely be
used. If existing outputs need preservation, the guard allocates a fresh
`.e3-builds/<mode>-<build-id>` directory in the checkout and the command prints
the actual output paths. The Linux output remains `E3-x86_64.AppImage`.
A separate wrapper is not required for storage protection.

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
trees, including `_internal` support files. Normal Windows, private installer
and launcher builds automatically use a fresh managed output directory when
legacy, changed or protected outputs prevent using the original locations.
Existing outputs and launcher selection stay unchanged. Source code, Python and
configuration inputs still come from the original checkout; the build does not
copy the checkout or a previous runtime. Generated metadata, specifications,
private seed, PyInstaller cache and final packages stay under the chosen output
root. The guard counts these managed directories in shared capacity accounting.
It can reuse a previously completed managed directory for the same source,
mode and installer name when its exact outputs remain unchanged, unselected,
unrunning and unpinned. Otherwise it allocates a fresh leaf. Failed managed
builds remain counted and reserved; nothing removes them automatically.

This fallback applies only to output conflicts. Budget, free-space, process,
pointer or inventory failures stop the build. Linux local builds retain strict
output checks. Do not remove state or user files to bypass protection. When a
Windows build uses its original output root, it replaces only its exact named
installer and preserves other files in `installer-dist`. `-PassThru` returns an
`E3WindowsBuild` result containing `SourceRoot`, `OutputRoot`, `Executable` and
`Installer`; ordinary console output also gives the actual installer path.

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
are preserved and a fresh managed output directory is used. Public builds
cannot silently consume private seed residue. Original household configuration
and calibration stay in their normal per-user locations. The Home installer
command prints the actual new installer path, including managed output paths.

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

The wrapper reads the canonical builder's actual result. Its legacy cleanup
registry accepts only the original worktree output layout; a managed output is
covered by the mandatory storage guard and is not incorrectly registered under
old paths. The wrapper reports when that optional registration is skipped.

## First-time metadata inventory

`build_storage_inventory.py` can report existing generated storage or initialize
shared state from an explicit list of checkout roots. It makes two complete
metadata passes and refuses initialization if either is incomplete or the
results change. It never opens artifact payloads, adopts old outputs, creates a
build lease or deletes files. Private files contribute names, identities, times
and logical sizes only. The resulting state records the baseline plus eight GiB
and starts with no owned outputs or leases.

For a read-only inventory of artifacts, choose a new report path outside the
listed roots whose parent directory already exists:

```powershell
python .\packaging\build_storage_inventory.py inventory `
  --root 'C:\absolute\E3-checkout' `
  --root 'C:\absolute\another-E3-worktree' `
  --report 'C:\absolute\existing-evidence-folder\new-storage-report.json'
```

An explicitly requested first-time initialization uses `init` with the same
roots and report arguments plus `--state '<absolute new shared state path>'`.
Existing reports and state are never overwritten. This is not a way to reset a
refused build or increase an existing ceiling.

## Applying the guard to another checkout

Existing worktrees keep their old scripts until these changes are integrated.
They do not automatically gain protection because another checkout uses the
shared guard. Integrate the reviewed normal-entry guard commit and managed
output follow-up together, in order, into a clean target branch. For the local
two-commit delivery, the sequence is:

```powershell
git cherry-pick 1b131d3 <managed-output-commit-from-handoff>
```

Alternatively, apply the cumulative patch supplied with the handoff using
`git am '<path-to-reviewed-cumulative.patch>'`. These are integration
instructions, not build commands; inspect and resolve any conflicts before
using the target checkout. Keep the guard, gate, inventory helper, Windows/Home/
launcher scripts, installer macros and ignore rule together. Run the focused
storage and entrypoint tests after integration. Then the normal commands remain
unchanged, for example `powershell -File .\packaging\build_windows.ps1` or
`BUILD_E3_HOME_INSTALLER.bat`; use the exact paths printed by the successful
build. No integration or build is performed by reading this document.

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
