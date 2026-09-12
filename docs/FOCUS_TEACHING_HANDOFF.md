# Focus teaching verification and handoff

## Focus teaching build selected and Pi installed (2026-09-12)

E3 DEV TEST now selects Focus teaching travel and approach, version 0.7.84,
revision 50b084565fe72a9d91260be736f77bdb2e7b0461. The required Windows build
script produced the executable and passed its native-library check. All 157
bundled source modules match the isolated checkout. Adjacent build-info and the
permanent pointer match. The selected EXE is
`.codex-worktrees/focus-teaching-dev/dist/E3/E3.exe`; the separate Inno installer
archive also completed successfully. Executable SHA256:
b219fbac28b9784cccd04d861e108a23177f41af046990abf857b01b999c1c93.

Windows focused tests: 231 passed; Linux/WSL Python3.10 simulated-backend tests:
162 passed; Ruff/compileall passed. Offscreen rendering confirms Z-minus is
available at Z6/contact5.124 with the new minimum. Exact-revision Fast CI
34700134366 is running; dependency, POSIX/recovery and Ruff jobs passed. The
previous preview-fix CI 34698833804 completed green. No full-CI success is claimed
for this revision yet. The operator explicitly approved the source push.

The operator authorized ending teaching and installing after tests. Pi kit
780412c3 was installed after exact-source preflight and service stop. One file,
machine/laser_focus.py, changed; eleven were already current. All twelve final
hashes match, existing configuration/max-Z/XY-offset/cooling hashes are unchanged,
and the service restarted active. Offline installer checks covered idempotence
and unknown-source rejection before any write. No firmware was changed.
A read-only post-install RPC reports DISCONNECTED, as expected after restart;
new connection, Home/park and reference are operator steps. No travel/probing/
laser firing was commanded for this update. New jog latency, actual gauge fit
and taught focus accuracy remain physically unverified. See
`dist/focus-teaching-0.7.84/START_HERE.md` and verification.json for handoff details.
