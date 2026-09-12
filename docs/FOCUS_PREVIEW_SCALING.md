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

## Operator handoff

This is a Windows desktop correction. The installed Pi companion and Ender
recovery firmware remain suitable; no service restart or firmware flash is needed.
Once the frozen build is selected, reopen **E3 DEV TEST**, open Surface / laser
focus, choose Position probe and click a solid spot. Check the coordinate preview
and actual path before the separate Move probe here action. Camera selection is
still a bed-plane estimate; raised surfaces are not corrected for parallax.

The exact frozen revision, executable, hashes, permanent launcher pointer and CI
result are recorded with the generated feature artifacts at handoff time.
