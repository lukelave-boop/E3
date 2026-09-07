# Physical validation after installation

All hardware operations in this procedure are operator-run. Keep the webcam
connected and working throughout. Keep laser emission physically disabled.
Firmware acknowledgements report commanded state, not measured voltage or motion.

## Start with readback

Read M115, M503, M119, M114 and M123 without motion. Compare M503 to the saved
working configuration, particularly 400 Z steps/mm, Z direction/mechanics,
probe Z offset and travel bounds. Do not blindly issue M502 or M500. The
firmware requires enough Z20 headroom for the complete native retract; an
unsuitable probe offset causes G39 to reject before deployment.

## Fans, independently

| Check | Native command | Expected M123 commanded state / physical observation |
| --- | --- | --- |
| Both off | `M106 P1 S0`, then `M106 P0 S0` | FAN1:0 FAN2:0; both stopped |
| FAN1 only | `M106 P1 S255` | FAN1:255 FAN2:0; only FAN1 runs |
| FAN1 half speed | `M106 P1 S128` | FAN1:128 FAN2:0; FAN1 PWM changes (fan starting threshold may vary) |
| FAN2 alongside FAN1 | `M106 P0 S255` | FAN1:128 FAN2:255; FAN1 retains its command |
| FAN1 off independently | `M106 P1 S0` | FAN1:0 FAN2:255; FAN2 continues |
| Finish off | `M106 P0 S0` | FAN1:0 FAN2:0; both stopped |

The connector names follow the rig's recorded FAN2/PA0 mapping, with PC0 assigned
to FAN1. Physical observation must establish the actual attached loads.

## Probe and Z

With at least 10 mm free below the pin, deploy once (`M280 P0 S10`) and stow
once (`M280 P0 S90`). Observe the actual pin and normal light. A high/triggered
M119 report by itself is not a deployment indication. Keep the probe stowed
for manual Z moves.

Use the existing native reference workflow over the solid border: initial
5 mm lift, native G28 Z R0 homing, leveling off, final lift to Z20. It deliberately
does not trust a made-up coordinate or use G92. Confirm the expected upward
direction and the native fast/retract/slow/stow cycle. The Creality XY motors
must remain disconnected because upstream Z-safe homing may establish virtual XY.

Once homed at Z20, request Z21 and back to Z20 at 300 mm/min. Observe the real
direction/distance and verify M114 at each end. The companion guarded manual
control limits individual moves to 5 mm and absolute Z20–80. It invalidates any
material reference; repeat reference before measurements.

## Material-height acceptance

Use `reference-border`, then `measure-height` once over the border to establish
the measured border contact. Jog the primary carriage above the thin piece and
measure, then repeat with the previously failing 7 mm piece. The host uses G39
only when the exact capability is advertised. Both fast and slow touches must
complete, stow and return to Z20. Thickness is material contact minus measured
border contact plus 1.5 mm. Test repeatability against independently measured
pieces before using the result as a correction. Missing contact is a failure,
never a height; no automatic repeat or fallback is performed.

## Accompanying E3 controls

The source accompanying this firmware adds `machine.mainboard` to the Pi's
existing authenticated MachineService. Install that software revision using
the existing E3 update procedure before these new CLI commands. Connect the
machine in E3 first; the CLI does not open a second serial owner or alter the
camera service. Examples, substituting the configured Pi host:

```text
python -m laser_aligner.mainboard_control status --host 192.168.5.18
python -m laser_aligner.mainboard_control fan1 50 --confirm --host 192.168.5.18
python -m laser_aligner.mainboard_control fan2 100 --confirm --host 192.168.5.18
python -m laser_aligner.mainboard_control fan1 0 --host 192.168.5.18
python -m laser_aligner.mainboard_control fan2 0 --host 192.168.5.18
python -m laser_aligner.mainboard_control z 21 --confirm --host 192.168.5.18
python -m laser_aligner.probe_diagnostic deploy --confirm-pin-clearance --host 192.168.5.18
python -m laser_aligner.probe_diagnostic stow --confirm-pin-clearance --host 192.168.5.18
python -m laser_aligner.probe_diagnostic reference-border --confirm-height-test --host 192.168.5.18
python -m laser_aligner.probe_diagnostic measure-height --confirm-height-test --host 192.168.5.18
```

The native command tables document the firmware protocol for operator physical
validation; they do not expand E3's manual-command allowlist. Normal software
control stays behind MachineService. STOP, disarm, disconnect and pre-job cleanup
attempt both fan OFF commands after identification of this firmware.

Record board identity, firmware manifest SHA, M503 configuration, attached load,
command and physical result for each check. Loss of USB, a stale webcam image or
an acknowledgement alone is not evidence that hardware stopped. This build is
ready for these tests; attached-hardware acceptance is what these tests establish.
