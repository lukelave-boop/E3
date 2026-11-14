# Saved material focus

E3 assumes the same material and supports remain in place. After a verified
clean shutdown, the next successful **Home / park XY** automatically selects
the saved material focus. There is no Reuse button or startup confirmation.
Normal job generation, preview, arming and clearance checks still apply.

Both measured and manually entered heights retain their material dimensions,
spacers and cut/raster focus policy. Changing material or supports is an operator
decision: clear the current workpiece height, then measure or enter the new
dimensions. Re-teaching the gauge is unnecessary unless its calibration changed.
If the axis or physical reference moved while off, use **Forget saved Z** and
reference again. E3 cannot detect those physical changes or unpowered drift.

This requires the matching desktop and Pi companion plus existing
`Cap:E3_Z_RESTORE_V1:1` Ender firmware. Closing Windows alone does not establish a
clean Pi shutdown. The verified shutdown must be idle, disarmed and at clearance.
The saved setup is pending until successful Home / park with verified current
Z at its saved clearance. Bare Home without a verified park defers selection.
No probing, Z travel or laser output is added by material selection.

STOP, failed operations, dirty shutdown, lost reference, changed calibration,
firmware, geometry, honeycomb datum or limits prevent reuse. Existing unknown-Z
and controller checks remain enforced. No physical acceptance test is recorded.

## Persistence and authority

The one-use Z checkpoint writes schema 2 when material settings are present.
Legacy schema 1 remains Z-only; mismatched or unsupported schemas are rejected.
The file is consumed before connecting, so a crash cannot replay yesterday's
clean record. Material data does not restore a job, camera measurement, preview,
old session or XY target. A new focus binding is created under the current
controller sessions, STOP epoch and freshly verified XY position.

This affects the desktop/Pi focus workflow and shared MachineService lifecycle.
It does not change `.e3laser`, either toolpath geometry pipeline, arming rules or
the hardware safety boundary. Software checks are not safety-rated.
