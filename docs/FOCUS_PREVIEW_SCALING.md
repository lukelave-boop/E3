# Focus camera preview correction — September 12, 2026

The Position probe error occurred before motion: E3 compared the transmitted
preview dimensions directly with the original camera dimensions. A read-only
sample from the actual Pi returned a fresh transcoded 1280×720 preview from a
1920×1080 source, which is the bridge's supported full-frame fallback.

The correction converts click coordinates back to the original source frame
before lens correction and the active bed map. The raw view remains appropriate
for this workflow; it does not need to display the corrected parked image.
The existing continuous image-edge coordinate convention is preserved for both
direct and reduced previews. Widget size and display DPI are presentation only.

Original source dimensions must still match the lens and bed calibration.
Unsupported source modes, inconsistent metadata, nonuniform resizing, stale
frames, changed camera settings, and out-of-bounds targets remain rejected.
Stream dimensions and mode are bound to the mapping signature. Choosing a point
only previews it; Move probe here remains a separate guarded request.

## Verification

- Actual Pi camera metadata was read without contacting the machine controller.
- 42 new regression cases cover the actual bridge fallback and real calibrated
  mapper, including lens distortion after conversion, source provenance and
  acceptance/rejection paths.
- The new cases include offscreen bridge → Qt image preparation → FocusBedView
  → AppContext integration at two window sizes and display pixel ratios 1 and 2.
- The combined mapping, focus view/dialog and raw monitor suites passed all
  194 cases on Windows. Focused Ruff and compileall passed.
- No hardware travel, homing, probing or laser output was commanded to test this
  correction. Physical placement and raised-surface accuracy remain unverified.

## Verified Windows feature build

E3 DEV TEST selects **Camera probe preview scaling**, version **0.7.80**,
branch `codex/marlin-material-height`, revision
`d20a379d0356eaf33505b00e25dfea5e1542e8c9`.
Target EXE: `C:\Users\lukel\Documents\E3\.codex-worktrees\focus-preview-dev\dist\E3\E3.exe`.
The required Windows build script completed. All 157 packaged E3 source
modules match the clean checkout, and adjacent build-info matches the validated
permanent pointer. EXE SHA256: `a0e9dacdccc0114b8a2090c1a36e67f4f3a86a89ee9a7b9eda4a8fa1de55005b`.

[Fast Development CI](https://github.com/lukelave-boop/E3/actions/runs/34698833804) is running after the test-only teardown synchronization; the full suite is not yet verified green.
CI revision `fb4bb7936b5015198d0921ecfb0ce3cdd19ac1b2` differs from the frozen revision only in tests/docs;
application, packaging and dependency sources are identical.
Local focused Windows verification passed 194 tests, including 42 new cases;
46 job/auto-home cases passed after the test-only synchronization. Ruff and
compileall passed. Widget tests were offscreen, not an interactive
frozen GUI or physical probe-placement test. The live Pi check read only camera
metadata. No Pi restart, firmware update or hardware motion was performed.

## Operator handoff

The first full Windows CI run passed 5,420 cases and failed one existing
auto-home test during teardown. The job-service and auto-home test sources were
unchanged by this camera fix. The test saw a durable stopped record before its
detached START handler released ownership, then immediately started shutdown.
Its fixture now waits for handler completion before teardown, while retaining
all cancellation and no-output assertions. This test-only synchronization does
not alter the frozen application. A concurrent production shutdown while a
terminal update is still finishing can attempt a terminal-state rewrite; that
separate existing job-shutdown race remains open and is not fixed here.

This is a Windows desktop correction. The installed Pi companion and Ender
recovery firmware remain suitable; no service restart or firmware flash is needed.
Once the frozen build is selected, reopen **E3 DEV TEST**, open Surface / laser
focus, choose Position probe and click a solid spot. Check the coordinate preview
and actual path before the separate Move probe here action. Camera selection is
still a bed-plane estimate; raised surfaces are not corrected for parallax.

The exact frozen revision, executable, hashes, permanent launcher pointer and CI
result are recorded with the generated feature artifacts at handoff time.
