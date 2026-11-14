# Camera Trace detection area status

## 2026-11-14 - Camera Trace detection area and topology recovery

Implemented on main f13b660, preserving automatic saved-material focus. The
desktop Trace panel offers Select detection area / Clear area for non-grid
tracing. A cyan rectangle limits existing hard-ROI/reference eligibility in the
unchanged camera frame. Crossing-edge objects are omitted; clearing restores
full eligible-view detection. Grid selection cannot silently ignore the box.
Selection is session-only and resets on new/open project, frame/calibration
change and invalidated camera evidence. Starting/clearing selection cancels old
requests and retires their results. Selection requires a displayed camera image.

Carries forward the bounded native-topology recovery candidate: one tighter
fit of unchanged mask/threshold data, retaining all validation and cancellation
guards. See docs/OBJECT_TRACE.md. No project schema, browser SVG, machine output
authority, motion/arming/STOP behavior, or Pi implementation changes.

Windows Python 3.14: 105 detector/controller tests and all 45 saved-material
regressions passed, plus 103 desktop Trace/drawing tests. Repository Ruff and
application compileall pass. Synthetic detector fixtures and injected/offscreen Qt checks only;
the operator's exact frame, interactive GUI and real camera/controller/laser
behavior remain unverified. Offscreen panel/canvas preview was visually inspected.
Compatibility CI, frozen build and permanent DEV TEST selection are pending.
Work is isolated under ignored build/detection-area-main because the shared
checkout contains unrelated edits; publication uses the connected GitHub API.

## Architecture

The rectangle is ephemeral desktop camera-review state. WorkspaceView emits
coordinates in the current displayed camera frame; the controller snapshots and
validates that rectangle per request, then appends its polygon to the existing
hard-ROI intersections. The trusted output polygon stays separate and unchanged.
No raster crop or coordinate transform is introduced. Existing empty-bed
reference eligibility and boundary-component rejection remain authoritative.

The historical CURRENT_STATE.md and ARCHITECTURE.md updates are prepared
locally but omitted from this publication because automatic approval review
rejected their full historical payloads. This concise note records the new
feature and verification without republishing those histories.
