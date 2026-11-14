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
Canonical Windows packaging run 43637808618 passed for application revision
47de94d275d24cbd390e8b996352b73fb91a52c7, version 0.8.54. The portable artifact
14131142348 is 333,989,897 bytes with SHA-256
75fbdc3a05ff81bd34e1d597b5c36622d488eef7a71e9ce4c6547d1c21c58799.
Frozen verification checked 203 archived modules, bundled QtCore and isolated
Windows profiles. Local download/selection remains pending after the Windows
execution environment disconnected; the last observed pointer still selected
Twin Sans 0.8.47. No app or hardware was launched.

Initial full compatibility had one outdated desktop test double without the
new workspace dependency; the test now provides it. Two unchanged Windows 3.10
startup/checkpoint tests also failed and are being rerun; POSIX and Ruff passed.
Fast desktop CI hit its existing 10-minute timeout without reporting a failure.
No application source changed after the verified build. Final compatibility is
pending; the temporary feature build workflow is removed.
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
