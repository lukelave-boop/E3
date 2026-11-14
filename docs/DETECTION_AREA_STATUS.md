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
No application source changed after the verified build. Final Compatibility CI
43639837813 passed on 05237c87cc07dc9de03357f7775adc193c2af6a8: Windows 3.12
9,778 passed / 69 skipped; Windows 3.10 7,658 passed / 193 skipped; POSIX 1,641
passed / 2 skipped; Ruff passed. Both prior unchanged 3.10 failures passed on
rerun. The temporary feature build workflow is removed. PR #68 is merged into
main as f5fcbc71008c8de67e506fb0d82edad8f2f3756b.
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

## Operator handoff remains pending

[Download the verified portable ZIP](https://github.com/lukelave-boop/E3/actions/runs/43637808618/artifacts/14131142348)
from the successful Windows build. GitHub artifact retention ends November 21,
2026. The ordinary E3 launcher is unchanged. The existing local helper verifies
the archive SHA-256, exact metadata and native-library profile, extracts the
bundle, and uses packaging/set_dev_test_feature.py for atomic selection. It does
not launch E3, connect hardware, or update the Pi.

Run in PowerShell after downloading the ZIP (adjust Archive if saved elsewhere):

```powershell
& "$env:USERPROFILE\Documents\E3\build\detection-area-tools\Select-Camera-Detection-Area.ps1" `
  -Archive "$env:USERPROFILE\Downloads\e3-windows-bundle-47de94d275d24cbd390e8b996352b73fb91a52c7.zip" `
  -Revision 47de94d275d24cbd390e8b996352b73fb91a52c7 -Version 0.8.54 `
  -Sha256 75fbdc3a05ff81bd34e1d597b5c36622d488eef7a71e9ce4c6547d1c21c58799 `
  -Size 333989897 -ArtifactId 14131142348 -RunId 43637808618
```

After a successful selection, open the permanent E3 DEV TEST launcher. In Trace,
turn off Use grid, choose Select detection area, draw a box with space around the
objects, and press Detect objects. Clear area restores the full eligible view.
The exact operator scene and physical performance still require review.
