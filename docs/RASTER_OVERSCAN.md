# Raster engraving surface and carriage travel

Preview playback identifies direction and laser state: amber means laser on,
cyan means laser off. Bidirectional raster output is unchanged.

Raster preview draws the initial laser-off approach as a separate red dashed line.
The orange boundary encloses job movement after that approach, including raster
run-up/run-out and row transfers. Full travel extents still drive fitting and
safety validation; generated motion, power, calibration and settings are unchanged.

Raster jobs distinguish the calibrated engraving surface from the machine's
carriage travel envelope. A honeycomb-local position is first rigidly placed in
machine coordinates. The controller target is then the desired beam position
minus `laser.spot_offset_x_mm` / `laser.spot_offset_y_mm`. The physical beam is
the controller position plus that offset. A machine travel limit must never be
entered as a honeycomb-local coordinate.

Positive output remains inside the calibrated surface and the existing guarded
output authority. The existing head/controller output check is retained too.
An explicit operator-confirmed surface-travel record can authorize carriage
movement over the entire calibrated surface. Its exact transformed polygon,
translated by the negative laser spot offset, is combined with the independently
verified carriage rectangle. Laser-off run-up, run-out, gaps and transfers may
leave that surface polygon only within the rectangle. The union is not replaced
with its enclosing rectangle. No path is clamped, no travel authority is inferred
from the camera image alone, and no setting is enlarged silently.

A 191 x 191 mm surface is measured in local coordinates. Its upper edge need
not be machine Y191: the saved origin and rotation are applied first. A saved
machine limit Y210 can therefore cut through a confirmed usable surface. The
surface-travel record resolves this disagreement without inventing clearance
beside that surface or changing the existing rectangle's Y limit.

## Explicit physical travel record

`machine.raster_travel_envelope` defaults to `null`. Its original required
fields remain:

| Field | Required value |
| --- | --- |
| `x_min`, `x_max`, `y_min`, `y_max` | Finite carriage limits in homed machine millimetres; minima strictly below maxima |
| `verified` | Exact JSON boolean; only `true` permits expanded raster travel |
| `controller` | Identified controller in the physical test record |
| `firmware` | Firmware identity/version tested |
| `configuration` | Tested configuration and reference to its recorded values or digest |
| `reference` | Exactly `homed_machine_xy_zero_work_offsets` |
| `tested_at` | Physical-test date, exactly `YYYY-MM-DD` |

Evidence strings must be nonempty and at most 512 characters. These fields record
operator evidence; the software does not independently qualify physical travel
or interpret a descriptive firmware/configuration record as live readback.
Confirm the complete XY rectangle, cable clearance and head/beam offset for the
current installation before marking it verified. Changes to controller,
firmware, mechanics, cabling, motion configuration or coordinate conventions
require renewed verification. Duplication of a saved machine clears this record.
Changing any envelope value or evidence invalidates prepared jobs through its
immutable digest. No existing user settings are populated by this feature.

The optional `surface` field is a separate physical confirmation for the current
calibrated surface, with exactly these fields:

| Field | Required value |
| --- | --- |
| `beam_polygon_mm` | Four exact machine-space beam corners from the saved rigid coordinate frame's `(0,0)`, `(width,0)`, `(width,height)`, `(0,height)` |
| `spot_offset_mm` | The current laser `[x,y]` spot offset; carriage polygon equals beam polygon minus this offset |
| `coordinate_frame_digest` | SHA-256 provenance digest of the current saved rigid coordinate frame |
| `verified` | Exact JSON boolean; only `true` permits surface travel |
| `confirmed_at` | Operator confirmation date, exactly `YYYY-MM-DD` |
| `evidence` | Nonempty operator physical-clearance statement, at most 2048 characters |

This confirmation concerns the head and cables over the entire surface under
the recorded controller, firmware and configuration. A visual calibration is
not physical-clearance evidence. The existing rectangle and its evidence stay
unchanged. Changing the surface, its provenance, the spot offset or its
confirmation invalidates prepared work; a different calibration cannot silently
inherit the record. A cloned machine has no inherited travel verification.

### Prepare a proposal without applying settings

`scripts/prepare_surface_travel_record.py` reads explicitly selected files and
creates a new JSON proposal. It never connects to hardware, performs a physical
test, starts the application or modifies a machine/calibration file. The
operator's confirmation date and evidence are mandatory; the tool does not
supply them. Example invocation from the source checkout:

```powershell
python scripts/prepare_surface_travel_record.py `
  --machine-registry "C:\review-input\machines.json" `
  --support "C:\review-input\1920x1080-manual-focus-010\honeycomb_support.json" `
  --bed-calibration "C:\review-input\1920x1080-manual-focus-010\bed_calibration.json" `
  --confirmed-at "<operator-confirmation-date: YYYY-MM-DD>" `
  --evidence "<operator's physical surface and head-clearance confirmation>" `
  --output "C:\review-output\surface-travel-proposal.json"
```

The active saved machine is selected by default; `--machine-id` selects another
saved machine explicitly. Support and bed files must share the selected
calibration-profile directory name, including when reviewing staged copies.
The helper checks four-corner evidence, bed creation-time binding and camera
profile provenance. It records SHA-256 hashes for all three inputs, machine and
profile IDs, the expected prior envelope digest, and the proposed complete
envelope. Existing output files and input paths cannot be overwritten.

The result is marked `PROPOSAL_NOT_APPLIED`. A separately authorized activation
must recheck the source hashes and prior digest and install the exact same
reviewed record on matching Desktop and Pi runtimes. Preparing a proposal does
not establish current camera, controller, firmware or physical-machine state.

The coordinate convention currently supports GRBL. Existing Home, zero active
G54–G59 XY/G92 offset checks, unchanged-offset verification, hardware process
authority and `machine.allow_motion` remain mandatory. Marlin execution of the
new raster contract fails clearly. A Home-only job start reads the current
position without moving if no trusted cached XY position exists, then validates
the initial approach. A readback does not grant homing authority.

## Exact-program enforcement

Prepared jobs contain one canonical `E3RASTER` application directive. Schema 1
records the four machine-space **beam** surface corners, the machine-space
**carriage** rectangle and SHA-256 of the complete physical travel record.
Schema 2 additionally records the exact verified **carriage** surface polygon
and the surface-record digest. It is part of
the immutable program digest and is never sent to the controller. Start Here
retains that directive. It must occur before any motion/laser enable and cannot
replace the final standalone M5. Unknown, duplicate or malformed directives are
rejected.

MachineService checks the recorded bounds/digest against current configuration.
The declared engraving surface must fit inside the existing configured output
authority, even if a particular image uses only its middle. This conservative
whole-surface gate may require correcting the configured/calibrated authority
before generation or execution; fitting powered pixels alone cannot widen it.

Every G0/G1 straight segment must fit wholly inside the applicable travel
authority: the rectangle for schema 1, or the exact rectangle/polygon union for
schema 2. Endpoint checks alone cannot establish containment in this potentially
nonconvex union. A transfer whose endpoints are valid but whose middle crosses
an unverified gap is rejected. Generated laser-off transfers may route through
the rectangle/polygon overlap; every resulting segment is validated. This covers
row reversals, white gaps, mixed-layer transfers and the actual initial approach. Positive
M3/M4 checks the current beam position. Positive G1 checks both segment ends,
including an inline S transition from zero to positive power. This prevents a
laser-off point outside the surface from starting a powered return into it.
G0 with enabled laser output remains rejected. Existing G21/G90/initial M5,
standalone final M5, power/feed ceilings, temporary arming, STOP/disconnect and
failure cleanup remain in force.

For measured-focus raster work, the approach drains and the focus descent
finishes before M4 S0 and the zero-power lead-in. A barrier/descent at the first
powered sample would consume the run-up and restart the head from rest; the
runtime avoids that sequence. A failed or stopped descent cannot continue into
the row. CUT focus behavior is unchanged.

The Pi companion must advertise `pi-raster-travel-envelope-v1` for the original
contract and `pi-raster-surface-travel-v2` for surface-authorized travel. It independently
validates canonical uploaded bytes and its locally configured travel evidence at
FINALIZE and START. Desktop/Pi records must match exactly. Older companions are
rejected before new raster jobs are uploaded. With no configured envelope,
legacy programs retain their restrictive output bounds and existing 25-field
wire-policy identity. Configuring an envelope extends that identity and requires
a matching companion even for ordinary jobs. An old program is never silently
granted expanded travel by the mere presence of the new setting. Existing
envelopes without a surface retain their original serialized fields and digest,
schema-1 behavior and capability requirement. Existing `.e3laser` project
schemas are unchanged. Schema-2 programs require matching surface-aware
Desktop/Pi code and records; an older runtime must reject them.

### Full-size images and outside-surface runout

The confirmed surface can be used at its full dimensions. A full-size image
with white borders can generate its engraving and runout entirely within the
verified union. A full-bleed pattern or an angled scan may require laser-off
runout beyond both the surface polygon and the independent rectangle. That
motion is still rejected: full-surface confirmation does not prove external
runout clearance. The planner keeps every powered sample and the required
lead distance; it does not crop powered coverage or truncate overscan to make
a job pass. Additional external travel needs separate physical evidence, or
process/scan settings whose complete path fits the existing verified geometry.

Deployment requires matched full desktop and Pi companion/source revisions.
Historical CPU-cooling, Z-control, thickness/operation-focus, capacity, large-job
start and two-million partial builders now reject selected sources that import
`raster_safety`; their old payloads omit the coupled configuration and protocol
update. Their compatible historical sources and pinned predecessor checks are
unchanged. The broader focus/material companion builders include their selected
sources' required shared modules, including `raster_safety`, with explicit
absent-only predecessor guards. The matching Pi runtime was installed on 2026-10-15 from selected revision
`aac45cd`, using a hash-guarded 64-file runtime dependency bundle. All replaced
predecessors matched reviewed Git revisions. See `CURRENT_STATE.md` for Linux
tests, preserved calibration and the activated operator-confirmed X travel record.

## Historical pre-activation inspection on 2026-10-15

Read-only inspection of the active **Ender 3 Tailscale** saved machine found a
191 × 191 mm honeycomb, machine work area X/Y10–210, park X15/Y195 and zero laser
spot offset. At this inspection, no verified raster travel record was saved. The configured fixed
output polygon extends to Y240.197779; it is not evidence of carriage reach.

The current saved four-edge support and bed map have matching creation bindings.
Its rigid origin is `(28.7714478853, 37.5035345157)` machine mm, X axis is
`(0.999978070214, 0.006622619658)`, and Y axis is
`(-0.006622619658, 0.999978070214)`. Park maps to local
`(-12.72810669, 157.58421468)`, which differs from the historical approximately
12.24 mm left-of-surface report. This inspection does not establish current
physical calibration accuracy or a fresh controller session.

The reported local movement rectangle X−2.1058–187.6838/Y3.00–184.60 maps through
that saved frame and zero spot offset to approximately machine
X25.4432–216.4313/Y40.4895–223.3424. It fits the historical X5–245 reach, but its Y
maximum exceeds the historical Y215 selection by **8.3424 mm**. These numbers
describe the reported rectangle, not a guarantee about a newly sampled/dithered
job or its speed-dependent lead distance.

The August 9 record reports X5–245/Y5–215 after park X15/Y195, with controller and
firmware identities unrecorded. It cannot establish a verified current whole
travel envelope. Y235 remains provisional; it is not adopted. No machine
movement, laser output, controller connection or active configuration change was
performed for this source implementation.

## Current activation and remaining acceptance

Later on 2026-10-15 the operator confirmed 6 mm extra X clearance on each
side and unchanged controller firmware. The saved record is now active on the
desktop and matching Pi: machine carriage X10..225.7672592962, Y10..210.
All Y settings and calibration were preserved. This is operator-reported X
verification; it does not establish new Y reach or speed-dependent run-up.
The historical numbers above must not be copied into current settings.

## Physical acceptance still required

Record the current controller, firmware, motion configuration, beam/head offset
and measured limits. Qualify the whole required path and cable clearance with
laser output physically disabled. Validate intended feed, acceleration and
run-up/run-out; a fixed 2.5 percent is not proof of adequate distance at every
speed. Then review the separate surface and movement footprint in Preview and
perform only small supervised sacrificial output tests under the installation's
existing procedures. These software checks are not safety-rated.
