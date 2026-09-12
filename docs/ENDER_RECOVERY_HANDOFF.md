# Ender recovery handoff — 2026-09-12

## Selected Windows build

Open the permanent **E3 DEV TEST** launcher. Its validated pointer selects:

- Feature: Ender connection recovery
- Version: 0.7.76
- Branch: `codex/marlin-material-height`
- Frozen revision: `51de9f4a18874c0ba1b9c11c1d87c1362335d90d`
- EXE: `C:\Users\lukel\Documents\E3\.codex-worktrees\ender-recovery-dev\dist\E3\E3.exe`
- EXE SHA256: `30db7942dd28d94423976fd49eece79bb85c5d3009bbcc576cc7d5219af39566`

The required Windows build script completed successfully. Adjacent build-info
and the permanent pointer match; all 157 packaged application source modules
match the isolated checkout. An offscreen Qt render checked unavailable and
recovered states, including the visible notice that a restart may move the
CR Touch pin. This was not an interactive test of the frozen application or
a real camera/probe alignment test.

## Pi software installed and checked directly

The existing Pi-specific SSH key connected to
`greenhouse-climate@192.168.5.18` with strict saved-host-key verification.
No passwords or private keys were copied or displayed.

`e3-pi-laser-focus-a1710770` was preflighted against the actual installed files,
then applied while `e3-hardware-node.service` was inactive with MainPID 0.
Six files changed with backups; six were already current. A subsequent dry run
confirmed all 12 desired files. The service was restarted.

Hashes of the machine configuration, saved 40 mm maximum, measured probe XY
offset and CPU cooling drop-in are unchanged. The live authenticated focus API
advertises `pi-laser-focus-recovery-v1`. It returns the configured 40 mm maximum
and probe offset `[3.302, 38.608]` during an Ender failure, with no fabricated
live Z, reference, surface or preview.

Actual checks on the installed service:

- The normal primary connection completed in 2.51 seconds, idle and disarmed,
  with `READY_HOME_REQUIRED`. It did not home or command axis travel.
- One explicit guarded Ender reconnect completed in 47.26 seconds. It reported
  no response from the Ender and retained its fault and configured values.
  No movement was replayed and the Pi API remained responsive.
- Stopping the updated idle service took 0.559 seconds and completed normally.
  The service was restarted. Startup still waits for the bounded Ender
  readiness attempt when the board is silent; the API then becomes available.

These are real connection and service-lifecycle results, not proof of recovered
Ender firmware, physical pin behavior, Z accuracy or a successful laser job.
The Pi companion retains its older base version label; file hashes and the
advertised capability identify this installed patch.

## Firmware prepared, not uploaded

Local package: `dist/e3-mainboard-f401-usb-0c719c71` and its ZIP.
It is also staged on the Pi at
`/home/greenhouse-climate/e3-mainboard-f401-usb-0c719c71`.

- Application SHA256: `fe9b02087d37c2019cfc54b16141f1f755e730f87837d535a51566898d6a5b6f`
- Combined SD SHA256: `0c719c7151ca7e39b617e190062170ffaef1c4f824578e95e394022030eeb871`
- Application vectors: `0x08020200`; image end: `0x0803483C`.
- Existing protected updater retained; application upload erases sector 5 only.
- Adds `E3_RECOVERY_V1`, retaining surface-height V2 and the 80 mm firmware
  ceiling. The configured 40 mm host limit remains unchanged.

All 12 manifest hashes and ZIP entries were checked. The application and ELF
match the audited native build. All 1,963 Marlin source files match the prepared
tree, all 70 archived E3 source files match the frozen commit with canonical
line endings, and all 26 pinned source hashes pass.

The earlier installed 9518b83f firmware does not parse serial commands in its
emergency kill loop. The observed silence is consistent with a halt but does
not prove it. A USB adapter reset is not evidence of a processor reset. The
operator confirmed that the Ender's 24 V supply is on. No firmware upload,
processor reset, USB power switch, pin actuation or motion test has been
performed during this deployment. A real processor reset may be needed before
the replacement can be uploaded. Do not treat another connection timeout as
permission to erase flash or replay an operation.

## Automated verification

Focused final checks include 66 offscreen UI tests, 155 host/package tests,
294 backend recovery cases on Windows and Linux, 2 token-to-focus integration
cases on each platform, and 17 final secondary-air-assist integration cases
on each platform. Three stale-job fixture tests also passed on both platforms
after their wrapper was updated to forward the new cleanup flags. These
counts describe overlapping focused suites and must not be added as a single
unique-test total. Ruff and compileall passed.

Firmware checks include 40 preparation/material tests, compiled production
parser checks, four fake-UART/GPIO scenarios, 16 ELF kill/minkill cases, and
18 planner checks with layout/startup audits. Emulated hardware checks do not
qualify physical recovery.

Exact frozen-commit firmware CI passed:

- [Compact F401](https://github.com/lukelave-boop/E3/actions/runs/34696173223)
- [Mainboard SD](https://github.com/lukelave-boop/E3/actions/runs/34696173094)

The [Fast Development run](https://github.com/lukelave-boop/E3/actions/runs/34696489090)
tests successor `f335e333183f34499b9f4c61b77c219f99126785`. That commit changes
only the stale-job test wrapper; application and firmware source are identical
to the frozen build. All four jobs passed: Windows Python 3.12 reported 5,379 passed / 25 skipped,
and POSIX transport/recovery reported 495 passed. Ruff and dependency/bytecode
checks passed. Compact CI reported 148 Linux startup and 533 Windows firmware
cases passed; Mainboard SD reported 88 Linux startup and 318 Windows firmware
cases passed. These suites overlap. This is automated verification, not
physical qualification.

The branch remains active pending physical qualification. Recovery does not
resume a job or restore a stale coordinate reference. See
[Ender recovery behavior](ENDER_RECOVERY.md) for the implementation boundary.
