# Development release review — 2026-10-15

Candidate: `codex/raster-overscan`, [PR #44](https://github.com/lukelave-boop/E3/pull/44).
Review baseline: `origin/main`, an ancestor of this branch. Before release
preparation, the accumulated branch comprised 60 commits and 215 changed files.
This is an integration review of the whole accumulated change, not only the
latest preview fix. The review preceded the authorized merge and publication recorded below.

## Findings and corrections

1. **Release blocker fixed:** the publisher previously ran independently of
   Compatibility CI. It now calls `./.github/workflows/ci.yml`, testing the same
   commit as the caller, before Windows/Linux packaging. Publish directly depends
   on compatibility and both packages. Default success conditions reject failed,
   cancelled and skipped prerequisites; no status override bypasses the gate.
   Both main push and main-only manual publication use this path. Contents write
   permission is confined to publication. Main has no branch protection; this
   change enforces the release dependency, not a repository merge rule.
2. **Documentation corrected:** the raster guide's pre-activation inspection was
   labelled current and said no verified envelope existed. It is now explicitly
   historical, with the later operator-reported X activation recorded separately.
3. **Known release limitations retained:** speed-dependent physical run-up,
   engraving quality, measured-plane placement accuracy, and the installed
   Windows update handoff are not qualified by automated CI. The release remains
   a development prerelease. The previous frozen build startup check is not an
   installed-update test.

## Review coverage

| Area | Boundaries reviewed and regression coverage |
| --- | --- |
| Raster planning and output | Powered surface versus carriage envelope; local-to-machine and beam/head offsets; serialized endpoints, row transitions, rotation, white/dither spans, edge pixels, acceleration floor, reversed rows and Start Here run-up. Raster safety, overscan, continuity, focus and golden-planning tests. |
| Local/Pi execution | Strict fast-parser fallback; power/feed/motion/hardware guards; immutable local validation proof and policy invalidation; fresh committed-byte checks; capability rejection; START session/STOP/disarm revocation during preparation; operation focus before raster lead-in; M5 bracketing and cleanup. Large-job, controller, remote/Pi, focus and rejection tests. |
| Setup and placement | Observational setup snapshots; compatible saved gauge visibility; capture/session/datum invalidation; retained support map; height-model evidence and independent qualification. Shared AppContext ownership and separate browser/desktop review bindings. |
| Persistence and authoring | Schema 5 reads schemas 1–4; cleanup rejected in older-schema image geometry; original raster assets unchanged; cleanup participates in preview identity; scoped layer profiles, undoable loading, polygon/stencil/distribution/rotation changes. |
| Desktop preview and UI | Immutable plan, separate approach line and movement box; powered extents and laser state in both scan directions; deferred rendering and visibility; saved-Z controls, modal/busy gates, stable scrolling and layout. Tests are offscreen, not interactive hardware acceptance. |
| Packaging and updates | Matched runtime dependencies and pinned predecessor rejection; historical partial Pi builders reject incompatible raster source; immutable package assets and recoverable manifest switch; public installer preserves existing user state. |
| Performance | Existing measured input-to-send call counts and timings reviewed; parser duplication regression retained. Preview ETA distortion and ACK throughput remain ranked follow-ups, not speculative control changes. See [performance review](RASTER_PERFORMANCE.md). |

## Verification

Release-gate/update tests: **71 passed in 0.91 s**. Repository Ruff, compileall,
whitespace checks, and checksum-verified actionlint 1.7.12 passed for both touched
workflows. The structural gate tests inspect real dependency edges and forbid
status overrides; they do not claim a live failing release was published/tested.
Compatibility CI [38809353488](https://github.com/lukelave-boop/E3/actions/runs/38809353488)
passed on release-gate commit `69d80c8`:

- Windows Python 3.12 desktop: **7,376 passed, 54 skipped in 628.98 s**.
- Windows Python 3.10 core: **5,900 passed, 141 skipped in 498.16 s**.
- POSIX controller/session: **652 passed in 103.40 s**.
- Repository Ruff passed; both firmware compilation workflows also passed.

Local Windows Python 3.14 testing exposed a sandbox restriction on loopback
sockets (WinError 10013); the restricted run was interrupted after stalling.
With loopback allowed, fail-fast testing passed 2,730 tests before stopping on
one watchdog timing assertion: successful process exit observed at 3.1025 s
against a 3.0 s threshold. All four watchdog tests passed in isolation in 7.27 s.
Neither shutdown behavior nor the threshold was relaxed. A complete local rerun
and the final documentation commit's CI results are recorded in PR #44 before
it is marked ready. Local Python 3.14 is additional evidence, not the supported
Windows 3.10/3.12 compatibility tier.

The already selected E3 DEV TEST 0.7.216 is revision `764f85d`. Its Compatibility
CI run [38801385074](https://github.com/lukelave-boop/E3/actions/runs/38801385074)
passed all four jobs. This preparation does not replace that build or its pointer.
The candidate machine/runtime, configuration parser and shared raster/focus
modules match the installed Pi source revision `aac45cd`; no Pi deployment is
needed for these release-gate/documentation changes. Older installations still
need a matched full companion/source update as described in the raster guide.

All 35 protected local machine/calibration file hashes matched their recorded
values during this review. No controller connection, motion, laser output, Pi
operation, or machine/calibration setting change was performed.

## Merge and publication handoff

After the final candidate's compatibility checks and review pass, merge PR #44
into main with explicit release authorization. The resulting main commit gets
its own complete compatibility run before packaging and publication. Verify
both immutable packages, manifest revision/checksums and the development release
result. Do not manually repoint the channel to a feature build. Delete the merged
branch when safe; preserve the active DEV TEST checkout until its pointer changes.

Keep project backups: a schema-5 project is not readable by older schema-3 E3.
Do not distribute personal calibration, credentials or travel verification as
release defaults. No machine setting needs changing to merge this branch.

## Publication outcome — 2026-10-15

PR #44 merged as `30f23b2a652fc8c918b2fce89e731322abafc4b6`. Development
**0.7.220** was published by [run 38816996249](https://github.com/lukelave-boop/E3/actions/runs/38816996249)
with every gate, build and publish job successful. Exact-commit checks: Windows
3.12 desktop 7,376 passed / 54 skipped (594.20 s); Windows 3.10 core 5,900 passed /
141 skipped (482.87 s); POSIX 652 passed (104.70 s); Ruff passed.
Independent main CI 38816996596 is also green after a single failed-job rerun
for a 20-second Node subprocess timeout; the same browser test passed in the
release gate and locally in 0.47 s. No timeout or safety behavior was changed.

Downloaded publication verification:

| Asset | Bytes | SHA-256 |
| --- | ---: | --- |
| `E3-Setup-30f23b2a652f.exe` | 219,904,197 | `6be8d5eaa3de1bfac4269f7982a9fcde74eabb77acf5cad5a86d6f346967a0ce` |
| `E3-30f23b2a652f-x86_64.AppImage` | 380,929,216 | `1cf8b5a04493193735040e8e5ae913cf656c8b830ee1fdb895d9edc862120d43` |

Both files match the stable manifest and GitHub asset size/digest metadata.
Release metadata, tag and manifest point to the merge revision. The normal
unauthenticated updater manifest fetch passed. Packages were not executed or
installed; physical and installed-update qualification limits remain unchanged.
All 35 protected settings/calibration hashes match. The merged feature branch
was deleted, preserving the selected frozen DEV TEST checkout and unrelated work.
