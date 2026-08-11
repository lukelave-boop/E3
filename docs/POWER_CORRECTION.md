# Power Correction

E3 Power Correction is a material-specific commanded-power bias near modeled
motion speed changes. It is inspired by the functional goal of commercial
systems such as Gravograph L-Solution; it is not a reverse-engineering of any
proprietary algorithm.

GRBL `M4` dynamic laser mode remains active and authoritative. GRBL still scales
PWM using its own planner and instantaneous motion speed. E3 does not replace or
reimplement that planner. Instead, E3 emits a small number of bounded inline
`S` changes on powered `G1` blocks around selected regions:

- `0` leaves the established `M4` program unchanged.
- a negative value commands less power in the correction region.
- a positive value commands more power in the correction region.
- straight cruising portions retain the layer's normal commanded power.

Positive correction can deliberately counter part of the power reduction that
would otherwise occur near a slowdown. It should therefore be tuned only with
small, supervised material tests. Power Correction is not a safety control and
does not make laser operation safe or unattended.

## Power mapping

For base controller power `P`, correction setting `C` from -100 through +100,
and local severity `s` from 0 through 1, E3 uses:

```text
bias = (C / 100) × 0.50 × s
corrected = round(P × (1 + bias))
```

The result is clamped to `0..power_max`. Correction magnitude 100 can therefore
change commanded power by at most 50 percent of base power at severity 1:

- `-100`: 50 percent of base
- `0`: base exactly
- `+100`: 150 percent of base, subject to `power_max`

The helper that owns this mapping is independent from path analysis so later
physical tuning can change the mapping without rewriting geometry planning.

## Vector correction

At each real vector junction, E3 compares the incoming and outgoing direction.
Straight continuation is zero degrees. Turn severity is:

```text
severity = (1 - cos(turn_angle)) / 2
```

This gives approximately 0.00, 0.15, 0.50, 0.85, and 1.00 for 0, 45, 90,
135, and 180 degrees. Open-path endpoints are not treated as corners; this
avoids inventing a correction zone on a single straight line. Closed paths do
include their closing junction.

The full-stop braking-distance estimate is:

```text
v = layer_feed_mm_min / 60
d = v² / (2 × configured_acceleration_mm_s²)
corner_zone = d × severity
```

The configured acceleration model is currently
`laser.preview_acceleration_mm_s2`. It drives both exact-job timing and Power
Correction zone length. On the currently inspected controller, `$120` and
`$121` both reported 500 mm/s², matching the configured 500 mm/s² value. That
readback verifies stored controller settings, not measured physical response.

Each side of a junction uses at most three collinear ramp blocks. Adjacent
corner regions are merged by taking the strongest local severity, and regions
are clamped to their segment. Added points closer than the generated G-code's
0.001 mm coordinate resolution collapse instead of producing duplicate moves.
Turn severity below 0.01 is treated as curve-tessellation noise so circles and
rounded rectangles do not acquire correction blocks at every tiny chord.
Original path vertices, endpoints, bounds, and straight-line geometry do not
change.

## Raster correction and overscan

Raster rows reverse with the laser off. Existing overscan is used first to put
acceleration outside the image. E3 compares laser-off lead-in and lead-out
distance with the same braking-distance estimate:

- if overscan covers the modeled braking distance, Raster Power Correction
  emits no image-area `S` changes;
- if overscan is short, only the uncovered portion just inside the image edge
  receives a bounded ramp;
- white gaps remain laser-off and do not become correction regions merely
  because `M5` changes optical output while motion continues.

Increasing safe, bounds-checked overscan is preferable to correcting image-area
power. Overscan remains included in design and controller bounds validation.

## UI, projects, and materials

The selected operation editor exposes **Advanced · Power Correction** with
precise Vector and Raster fields from -100 through +100. Edits use the existing
project undo/redo command path. Material presets persist the same two fields.

The fields are additive within the current `.e3laser` schema. Older projects
and material databases receive zero defaults. Saving and reopening preserves
explicit values.

Exact Job Preview reports `V` and `R` correction settings per operation and
parses every generated inline `S` value, so its maximum-power and power-shading
views reflect the exact corrected program. Raw G-code retains `M4`, uses bounded
inline `S` only on powered `G1`, keeps rapid travel laser-off, and retains the
initial and final `M5` bracketing.

## Tuning guidance and limitations

Start at zero. Use a restrained sacrificial sample of the actual material and a
simple shape containing long straights and representative corners. Compare
small steps such as -10, 0, and +10 while keeping speed, focus, power, airflow,
and material fixed. Inspect both straight density and corner/reversal density.
Do not infer a broadly safe setting from one material or speed.

The zone model deliberately uses braking distance and corner severity rather
than duplicating GRBL junction-deviation and lookahead behavior. Actual motion
depends on firmware, mechanics, junction deviation, segment length, queueing,
and controller configuration. E3 therefore predicts where a material bias may
be useful; it does not claim exact instantaneous velocity or optical power.
