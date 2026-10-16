# Saved raster-travel compatibility

The first engraving-editor build could reject an existing saved machine profile
with `Unknown configuration key: machine.raster_travel_envelope`. Its source base
predated that field. The repair includes the existing parser and its coupled
execution contract; it does not remove the record, ignore it or widen limits.

Configuration and saved profiles accept an optional strict record containing
finite carriage bounds, an exact boolean verification flag, controller, firmware,
configuration, coordinate-reference and physical-test-date evidence. Unknown
keys, invalid numbers and malformed evidence are rejected. Absence defaults to
no expanded travel. Duplicating a machine clears the physical verification record
for the new identity. Loading the original preserves its record.

The calibrated engraving surface and carriage travel bounds remain distinct.
Only laser-off run-up, run-out, gaps and transfers may use separately verified
travel beyond the surface. Positive output is checked against both the surface
and the existing guarded output authority, including the physical beam offset.
The source image's exclusion mask is applied after dithering and before clearance
construction; extra travel cannot restore power to excluded pixels.

One canonical E3RASTER application directive binds the surface, carriage bounds
and complete evidence digest to the exact program. It is never transmitted as
a controller command. Validation covers previous and next segment endpoints,
the actual initial approach, zero GRBL XY work offsets and current job authority.
Invalid, stale, missing or unverified records fail closed. Restart preserves the
directive and the required row run-up. Envelope changes invalidate prepared jobs.

Remote execution requires a companion advertising pi-raster-travel-envelope-v1
and matching local policy evidence. Older or mismatched companions reject before
the job can start. The imported source contract matches the existing raster-travel
implementation; this repair does not deploy or contact a Pi. Existing homing,
motion authority, temporary arming, M5 bracketing, stop and failure cleanup remain
mandatory. These software controls are not safety-rated.

Regression checks use synthetic records and simulated transports. Read-only
validation of the user's existing configuration confirms compatibility without
constructing runtime services or writing configuration. Neither test establishes
new physical travel accuracy or authorizes movement.
