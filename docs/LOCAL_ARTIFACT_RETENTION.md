# Local build storage and artifact retention

Normal local Windows, private Home and DEV TEST launcher builds always use
managed outputs under `.e3-builds/<mode>-<build-id>` in the source checkout.
Keep using the existing commands and parameters; each command prints its actual
output paths. The checkout and its Python environment remain inputs. Existing
legacy bundles and launcher selection are preserved. A separate wrapper is not
required for storage protection.

## Default build admission

The managed policy has a fixed **eight-GiB cap** shared across its recorded
managed containers and active reservations. Every byte under those `.e3-builds`
containers counts, including unknown files, failed builds, cache and temporary
files. Admission reserves two GiB per build and requires a two-GiB free-space
floor. Reservations are admitted atomically across participating checkouts.

`legacy_total_bytes` is `null`: unknown and outside this eight-GiB pool. Normal admission does
not discover old worktrees, scan legacy bundles or measure source `.venv`
directories. Eight GiB is not a claim about all existing E3 storage. A new normal
build does not need a whole-machine legacy baseline to start.

An unexpectedly large build may exceed its reservation; completion measures real
managed usage and retains the reservation if the budget or free-space floor
refuses completion. This is admission accounting, not a filesystem quota. The cap never grows automatically
to excuse excess usage, and no automatic deletion is enabled.

On Windows the state defaults to `%LOCALAPPDATA%/E3 Build Storage/state.json`.
Do not reset state to bypass a refusal. Schema 2 identifies the policy as
`managed_outputs_v2` and remembers the participating source roots and managed
container identities. It refuses an incompatible legacy-baseline state; changing an existing
policy requires separate review. It does not prune Git history or remove source,
projects, profiles, calibration or evidence. Inaccessible managed accounting
fails closed.

## Ordinary rebuilds

An explicit normal build may reuse its own previous successful managed directory
for the same source, mode and installer name. The guard requires prior ownership,
unchanged identities and contents, and current launcher/process/pin checks.
It verifies protection immediately before replacing that build's exact outputs.
Otherwise it creates a fresh managed leaf. No fresh checkout is required.

Selected, running or rollback-pinned bundles are protected as whole output
trees, including `_internal` support files. Legacy output locations are never
chosen by normal local builds, even when empty. Source code, Python and
configuration inputs still come from the original checkout; the build does not
copy the checkout or a previous runtime. Generated metadata, specifications,
private seed, PyInstaller cache and final packages stay under the chosen output
root. Failed managed builds remain counted and reserved; nothing removes them
automatically.

Budget, free-space, process, pointer or inventory failures stop the build.
Linux retains its separate strict output checks. Do not remove state or user
files to bypass protection. A Windows build replaces only its exact named
installer within its chosen leaf. `-PassThru` returns an
`E3WindowsBuild` result containing `SourceRoot`, `OutputRoot`, `Executable` and
`Installer`; ordinary console output also gives the actual installer path.

An interrupted or failed build retains its lease and reservation for review.
Only successful completion records new ownership and releases the reservation.
The guard does not add background pruning: replacement is limited to the known
outputs of the build explicitly invoked. Older worktree builds remain present
until a separate exact cleanup manifest is approved. Older checkout scripts do
not participate in leases; never run them concurrently in the same checkout.

## Dependencies and temporary files

Local commands use already installed PyInstaller, E3 desktop dependencies and
Inno Setup 6. They do not run `pip install`, upgrade packages, invoke `winget` or
install Chocolatey packages. Read-only module and compiler checks fail before a
lease if required tools are absent, with an explicit setup instruction.
Preparing or changing those dependencies is a separate action. The source
`.venv` is not adopted as managed build output or charged to the managed pool.

After admission and verification, child processes inherit `TEMP`, `TMP` and
`TMPDIR` under the leased `temp` directory. PyInstaller, pip, Python bytecode and
user-package cache locations point under its `cache` directory; user-site
imports and automatic bytecode writes are disabled. The same environment covers
metadata generation, bundling, private seed preparation and Inno compilation.
The caller's environment is restored on success and failure. Pre-admission
Python probes and guard invocations also suppress bytecode writes, so importing
the guard does not create `packaging/__pycache__` in the source checkout.

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
The workflow installs PyInstaller in its explicit disposable-runner dependency
step. Windows packaging itself only consumes existing tools; its temporary and
cache directories remain within the leased CI output root.

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
from the normal builder's fixed eight-GiB managed pool. Missing rollback
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

## Optional wrapper

`build_with_retention.ps1` defaults to inventory only. Its explicit `-Build`
mode delegates directly to the managed-output builder without scanning legacy
output locations or requiring a cleanup inventory. Its `-Python` selects the
registry interpreter, while the builder retains checkout-venv/Python selection.
Its legacy budget parameters cannot change the fixed managed cap or reservation.

The wrapper reads the canonical builder's actual result. Its legacy cleanup
registry accepts only the original worktree output layout; a managed output is
covered by the mandatory storage guard and is not incorrectly registered under
old paths. The wrapper reports that the legacy cleanup registry is unchanged.

## Separate legacy metadata inventory

`build_storage_inventory.py inventory` is a separately requested advisory report
over explicit legacy checkout roots. It makes two complete metadata passes and
reports incomplete or changing results. It never opens artifact payloads, adopts
old outputs, creates a build lease or deletes files. Private files contribute
names, identities, times and logical sizes only. This report is optional and is
not required by normal managed build admission.

For a read-only inventory of artifacts, choose a new report path outside the
listed roots whose parent directory already exists:

```powershell
python .\packaging\build_storage_inventory.py inventory `
  --root 'C:\absolute\E3-checkout' `
  --root 'C:\absolute\another-E3-worktree' `
  --report 'C:\absolute\existing-evidence-folder\new-storage-report.json'
```

The helper's older `init` command creates a legacy schema-1 baseline state and
is not setup for the managed policy. Do not point it at the normal managed state
path. Existing reports and state are never overwritten. An incompatible existing
state must be reviewed explicitly, not silently reset or used to raise the cap.

## Applying the guard to another checkout

Existing worktrees keep their old scripts until these changes are integrated.
They do not automatically gain protection because another checkout uses the
shared guard. Integrate the complete reviewed commit sequence from the handoff,
including the final managed-only policy, into a clean target branch:

```powershell
git cherry-pick <ordered-reviewed-commits-from-handoff>
```

Alternatively, apply the cumulative patch supplied with the handoff using
`git am '<path-to-reviewed-cumulative.patch>'`. These are integration
instructions, not build commands; inspect and resolve any conflicts before
using the target checkout. Keep the guard, gate, inventory helper, Windows/Home/
launcher scripts, installer macros and ignore rule together. Hosted CI also
needs the explicit PyInstaller dependency step. Run the focused
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
