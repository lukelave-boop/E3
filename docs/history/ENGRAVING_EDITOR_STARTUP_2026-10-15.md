# Historical engraving editor startup evidence (2026-10-15)

The following is the verbatim conflicting startup/handoff record from
`codex/engraving-editor` at `a3f038b06c74cc75b385d50973dfccbeaf516504`.
It describes that branch at the time, including its failed CI, selected builds
and subsequently superseded preview-golden expectations. It is not the current
launcher selection, an instruction to change a launcher, or a passing integration
result. Main's current preview metadata contract remains authoritative.

---

## Engraving editor startup repair 0.7.211 handoff (2026-10-15)

E3 DEV TEST now selects Engraving image editor, version 0.7.211, branch
codex/engraving-editor, exact frozen source revision
`ca6af32752c95c4e8c5f02c20f6fa50bc58a1af4`. Target:
`C:\Users\lukel\Documents\E3\.codex-worktrees\engraving-editor-startup-build\dist\E3\E3.exe`.
The permanent pointer was changed atomically with packaging/set_dev_test_feature.py
and read back through the launcher validator. Close the earlier startup error
and reopen the permanent E3 DEV TEST launcher. The normal installed application,
launcher, shortcut, and previous feature bundle remain intact.

The canonical Windows packager passed its native DLL guard. All 189 packaged
application modules and three resources match the exact frozen commit. EXE SHA256:
`0dcf586b245eadc03bbde78058b3903614818bab3dd0a3b5ca3458399ed96dd5`.
The canonical installer also completed at installer-dist/E3-EngravingEditor-Setup.exe;
it was not installed and is not needed to use the permanent feature launcher.
A frozen offscreen check with a saved machine registry and synthetic travel
envelope completed in 5.779 seconds with both Qt main-window visibility markers
and no startup error modal. The old 0.7.209 executable fails this same check with the
reported configuration dialog, confirming that the check detects this regression.
The fixture config and registry remained byte-identical. Test state and credentials
were isolated; production Windows Qt preference writes were denied by the sandbox.
All 39 protected real configuration/calibration/state file hashes, sizes and
modification times remain unchanged after both checks. This is offscreen startup
verification, not interactive operator or physical hardware verification.

Compatibility CI run 38818112075 on the exact frozen source completed: Windows
Python 3.10 had 5972 passed, 144 skipped and 16 failures; Python 3.12 desktop had
7464 passed, 54 skipped and 17 failures. Lint passed, and the POSIX simulated
transport/session tier passed all 652 tests. Thirteen failures in each Windows
tier were preview golden files missing the imported contract's powered_bounds_mm
and raster_boundary fields; the remaining failures match the previously recorded
three command-budget checks and desktop retained-Z visibility assertion.

The 13 curated preview expectations now include exactly those two metadata fields.
Review proved all older preview values (including moves and timing), generated
programs and result.json files unchanged; the 26 non-preview artifacts are byte-
identical. Powered bounds were independently checked against existing laser-on
moves. Focused planning-golden and boundary-preview checks pass: 49 passed,
2 known aggregate-budget cases deselected. Repository Ruff passes. This follow-up
changes only test expectations and this verification record; application code,
the exact frozen revision, and the selected EXE are unchanged. Full CI was not
rerun after the expectation-only correction and is not reported green. The
remaining baseline failures still prevent main integration; no merge or release
is made.

## Saved raster-travel startup compatibility repair (2026-10-15)

The operator reported that editor 0.7.209 rejected the existing active machine
profile's machine.raster_travel_envelope field before opening. The editor's
background-cleanup base predates the already implemented raster-travel contract.
The exact error was reproduced through the desktop startup validation boundary.

This repair ports only the strict parser and coupled configuration/profile,
program identity, raster generation, local/remote validation and focus contract
from the existing raster-overscan implementation. The full-branch merge was
rejected by automatic approval review; the narrower runtime/test patch was
approved. Release workflows, deployment tools and display-only preview changes
are excluded. No original configuration field is dropped, ignored or enlarged.
Image source/alpha identity checks, post-dither eligibility, editable outlines
and inactive cutting snapshots are retained. Clearance is constructed only
after masked spans have been generated.

Read-only validation now accepts the actual saved config and all three profiles;
the active envelope matches the main configuration. All 39 protected saved
configuration/calibration file hashes remain unchanged. Twelve new startup
regression cases plus existing startup recovery checks pass (21 tests), as do
171 combined image/travel/safety checks and all 486 editor regression tests.
All 426 configuration/profile/startup, asynchronous desktop job, plan, focus,
remote-service and simulated Pi-server checks pass (240.61 seconds). Repository
Ruff and compileall pass. The fresh frozen executable and replacement startup
check pass as recorded above. The replacement frozen smoke fixture
includes a saved machine registry and synthetic unverified envelope, motion off,
camera off, power zero and a nonexistent serial port.

No machine connection, Pi contact/deployment, settings/calibration edit, motion
or laser output has been performed. See docs/RASTER_TRAVEL_COMPATIBILITY.md.

## Historical engraving image editor 0.7.209 handoff (2026-10-15)

E3 DEV TEST selects Engraving image editor, version 0.7.209, branch
codex/engraving-editor, exact frozen source revision
`d21145c6633d7a30f475d770d87d6238960d640e`. Target:
`C:\Users\lukel\Documents\E3\.codex-worktrees\engraving-editor-dev\dist\E3\E3.exe`.
The permanent pointer was updated atomically with packaging/set_dev_test_feature.py
and read back through the launcher validator. Save work, close the old E3 window,
reopen the permanent E3 DEV TEST launcher, select one image, and choose
Objects -> Image -> Edit image for engraving. The normal launcher, shortcut,
installed application and existing running operator process were left intact.

The canonical Windows packager produced the EXE and passed the native DLL guard.
All 188 bundled application modules and three resources match the exact commit.
Frozen offscreen startup loaded native Qt using isolated test configuration/data,
camera autostart off, motion disabled and a nonexistent serial port. EXE SHA256:
`ba64f9d81e73569eb094ae65a8a8cf7f94bb1dddaa862d0c7f14ce2c47f74e80`.
The canonical packager also completed installer-dist/E3-EngravingEditor-Setup.exe.
The installer was not installed. No interactive operator or physical
camera/controller/laser verification is claimed.
