# Native Marlin material-height prototype

This is a pinned Creality Marlin patch, separate from `firmware/ender_aux/`.
It reuses Marlin's physical probe handling. It is not the BENCH simulation and
does not replace the working machine's controller or Pi software. The webcam
remains connected and part of the system. Physical probing of this firmware
has not been verified; see `CURRENT_STATE.md` for exact spare-board evidence.

## Fixed V1 contract

- `M115` reports exactly `Cap:E3_MATERIAL_HEIGHT_V1:1`.
- `M119` includes the existing E3-compatible `test_axis_known_x/y/z_flag`
  read-only fields, derived from Marlin's axis trust flags.
- `G39` takes no arguments. It probes at the current XY, without positioning XY,
  homing, changing coordinates, changing heaters/fans, or retrying.
- Start at native Z20, absolute millimetres, trusted Z, leveling disabled,
  no Z workspace offset, and a logically stowed probe. The operator's physical
  clearance confirmation remains necessary in the application workflow.
- Contact range is **-2 to +10.5 mm in the native border frame**, independent of
  the bed-leveling clearance settings. +10.5 corresponds to a 12 mm sheet only
  for the recorded support 1.5 mm below the border. This is a software range,
  not a physical accuracy or clearance qualification.
- Before deployment, the complete range plus the native retract must fit below
  Z20 after accounting for probe Z offset. The current 5 mm retract admits
  offsets down to -4.5 mm. Insufficient headroom rejects before deployment.
- Marlin owns deployment, fast touch, the unchanged native retract distance,
  slow touch, and stow. Both touches must be in range. Descent stops at the
  stricter of the native low-point setting and -2 mm contact height. Missing
  contact fails; native stow is attempted after deployment even on failure.
- The result uses Marlin's native two-touch weighted average (40% fast, 60%
  slow). Ordinary G28/G29/G30 retain their original sampling and clearance
  behavior, including the published configuration's extra bed-probe sample.
- Success is `E3MH:1 Z:<finite height>`. Failure is `Error:E3MH:1 ARGUMENTS`,
  `PRECONDITION`, or `PROBE_FAILED`; an `ok` alone is never a result.
- G39 omits G30's fixed post-probe clearance move, which can descend toward a
  raised surface. E3 checks the returned state, commands its guarded Z20 lift,
  and verifies it before accepting a border reference or measurement.

`MachineService` retains all existing admission, session, STOP and reference
invalidation rules. `CrealityZProbe` selects G39 only for the exact V1 identity,
and rechecks that identity before each contact. Stock Marlin retains the tested
G30 CLI path. Changed, duplicate or unsupported E3 capabilities reject; a failed
G39 never falls back to G30. Both the border check and material use the same
method. No desktop controls are unblocked and no camera correction is applied.
The software is not safety-rated. This baseline still reports no emergency parser.

## Raised-surface V2 contract

The exact `Cap:E3_SURFACE_HEIGHT_V2:1` capability adds
`G39 C<clearance> H<maximum_contact>` without changing bare G39/V1. Both
parameters are required exactly once, in either order. Values use finite decimal
syntax; unknown/duplicate parameters, exponents, missing values and trailing junk
are rejected. Clearance is 20..80 mm and maximum contact is -2..65 mm, both
in the border-homed native frame. Minimum contact remains -2 mm.

V2 requires current Z within 0.05 mm of the requested clearance, never above Z80,
plus the same trusted, absolute-mm, stowed-probe, zero-Z-workspace-offset and
leveling-off preconditions. The requested upper contact, runtime probe Z offset
and native retract must fit at or below the current clearance. Native deployment
headroom is checked independently. A maximum contact is a limit, not a promise
that a surface is clear of the tool or the raised pin.

The temporary envelope exists only inside that one native fast/retract/slow/stow
cycle. Both touches must lie inside it. Every ordinary return, including deploy,
contact and stow failure, restores V1 defaults; a nested call rejects. Neither
version homes, moves XY, changes coordinates, nor performs a fixed final lift.
The caller verifies success, raises to clearance through its guarded normal
controller path and verifies that position.

V2 returns `E3MH:2 Z:<contact to three decimal places>` or
`Error:E3MH:2 ARGUMENTS`, `PRECONDITION`, or `PROBE_FAILED`. M115 also reports
`E3SG:2 PROBE_Z:<runtime offset to six decimals> RETRACT:<native retract to six decimals> MIN:-2 MAX:65 CEILING:80`.
The host can bind calibration to that geometry; the compact profile additionally
advertises and enforces `E3_Z_LIMIT_80_V1` in the actual planner. Software tests
do not establish attached-mechanics behavior or measurement accuracy.

## Source and loader layout

`baseline.json` pins Creality `s1_pro_plus` at
`7fffa9270ff8fb5ee208a5b04f832e20bab19c60`, PlatformIO and compiler/framework
versions. This published 2.0.8.24F4 source is not claimed to match the working
machine's installed 2.0.8.26F4 binary.

- `baseline.patch`: select F401 and fix the upstream stray `/` syntax error.
- `material.patch` plus `overlay/`: bounded material cycle and host reporting.
- `layout.patch`: use the identified RET6's 512 KiB flash, retain a conservative
  64 KiB RAM profile, link vectors at **0x08020200**, and wrap CMSIS SystemInit
  to restore interrupts after the retained updater's masked handoff. It also
  disables the unrelated laser feature and automatic EEPROM initialization.

The existing updater and stock loader are not included or overwritten by the
application image. `package.py` audits allocated ELF sections, reset control
flow, vector table, RAM use, and image validation. It executes the actual
SystemInit wrapper in Cortex-M4 emulation with fake RCC/SCB registers, verifying
VTOR and interrupt restoration. It packages only allocated load sections;
ELF file headers are not flashed. Empty binary gaps are padded with 0xFF.

## Reproduce the software checks

Use Python with PlatformIO 6.1.18 and the test dependencies listed in
`../ender_aux/requirements.txt`. Prepare an isolated checkout at the exact
baseline revision, then run from the E3 root (these commands never flash):

```text
python firmware/marlin_material/build.py build/marlin-material-height --core build/platformio
python firmware/marlin_material/run_native_tests.py build/marlin-material-height --toolchain build/platformio/packages/toolchain-gccarmnoneeabi/bin
python firmware/marlin_material/package.py build/marlin-material-height/.pio/build/STM32F401RC_creality/firmware.elf --objdump build/platformio/packages/toolchain-gccarmnoneeabi/bin/arm-none-eabi-objdump.exe --output dist
python -m pytest -q tests/test_marlin_material.py tests/test_native_height.py tests/test_native_probe.py
```

Use `arm-none-eabi-objdump` without `.exe` on non-Windows build hosts.
Preparation never resets or cleans a checkout and refuses changed overlay files.
The native harness executes the actual patched run_z_probe, material wrapper
and G39 functions with fake physical I/O in two-sample, three-sample and
three-with-one-extra bed configurations. It tests both acceptance and rejection,
offset conversion, native feedrates/retracts and unchanged ordinary probing.
This is neither an interactive GUI test nor a real probe/motor test.

## Maintenance and recovery boundary

Keep `dist/ender-aux-0.1.0-177e5965` and `dist/ender-aux-0.2.0-f96d3a21` intact.
The updater's normal host supports those E3 identities. A running Marlin image
does **not** understand its ASCII `INFO` / `UPDATE` protocol. Identify Marlin
with M115 first. On this pinned STM32 implementation, explicit M997 resets into
the retained updater; HOLD must arrive within its startup window before using
the existing uploader. This is a maintenance transition, not automatic runtime
recovery, and invalidates any coordinate reference. Never retry a measurement
across it. Cold restart also exposes the updater's command window.

No flashing or serial maintenance occurs on import, preparation, build, tests,
or packaging. Live maintenance requires a separate explicit hardware operation
on the positively identified spare. A bare spare can establish communications
and unhomed rejection, but cannot establish physical contact accuracy, pin
deployment, motor stopping, or electrical output behavior.
