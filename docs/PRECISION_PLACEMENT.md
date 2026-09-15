# Precision placement and measured surface height

## Target and present status

The acceptance target is **0.10 mm maximum radial XY placement error, including
measurement uncertainty**, for known templates and shapes on one flat surface
parallel to the support plane. The intended interval reaches 20 mm above the
saved honeycomb, counting the workpiece and all spacers. This is a target for
physical qualification, not an accuracy claim for the current camera or machine.

No area or height interval has been physically qualified by this implementation.
Historical calibration passes and errors at fitted points do not qualify it.
Existing diagnostic calibration thresholds retain their original meanings.
Arbitrary outline tracing and finished cut dimensions require separate checks.

## Daily use

**Measure surface → Capture for placement → optional Align to workpiece →
place designs → Preview → Run.**

1. Use the existing guarded reference and measurement actions. Enter total
   spacers before measuring. The camera uses the measured top elevation relative
   to the named calibration datum. It does not use sheet thickness or focus Z.
2. Choose **Capture for placement** with the workpiece restrained. This uses the
   established Home / park and capture sequence. Review that photograph before
   aligning or placing known geometry.
3. Precision placement keeps translation and rotation continuous and suspends
   grid snapping. Known template dimensions remain fixed. **Align to workpiece**
   proposes a rigid match against the captured visible features for review;
   ambiguous detections reject. Matching confidence is not placement accuracy.
4. Review the prepared design and job. A new measurement, camera change, model
   change, interrupted session, or incompatible reference invalidates the
   photograph and job review. Project objects keep their existing dimensions
   and coordinates; capture and review again on the new surface.

The unmeasured probe-selection view, when needed, uses the support plane and is
explicitly approximate. It cannot authorize precise placement. A measured height
outside the accepted interval is rejected rather than extrapolated.

Height-aware jobs require the matching Pi capability as well as the updated
desktop. An old Pi cannot validate their surface binding and rejects them. All
motion, probing, arming and laser output still pass through `MachineService`.

## Guided setup

Open **Tools → Machine Setup… → Setup wizard**. The embedded wizard uses the
existing setup controls across eight guided stages. **Back** and
**Next** browse stages; they do not run actions, save evidence or complete a
calibration. Use the explicit tool and prerequisite buttons for each stage.

1. **Restrain the setup:** secure the camera, cable, honeycomb and workpiece;
   lock camera mode, focus and exposure. Review camera readiness and inspect
   physical restraints separately.
2. **Check lens calibration:** collect and review distributed sharp lens
   evidence at the production resolution.
3. **Establish the support map:** establish the guarded keyed bed mapping
   and observed support boundary at the repeatable photo pose.
4. **Survey bed leveling and datum:** measure a rigid target of independently
   known thickness at four corners and center. Each position requires a new
   reading. Review the inferred honeycomb elevations and their span, adjust
   the supports manually if needed, and repeat. Copying the surveyed mean
   into the datum editor does not save it; use the explicit datum Save action.
5. **Measure, return and teach focus:** measure a solid patch, return the laser
   to that measured spot, then use the existing gauge teaching action. Disabled
   actions explain their prerequisite beside the relevant control.
6. **Assess one-height repeatability:** use **Prepare holdout marks** to open
   Accuracy Validation and prepare its target through the existing guarded
   marking controls. Assess ten still and ten Home / park captures at one
   reference plane before interpreting height compensation.
7. **Collect height calibration:** acquire fresh lower, upper and independent
   middle-height observations. These do not replace the working support map.
8. **Record independent XY checks:** record physical XY observations,
   uncertainties, tested coordinates, area and heights.

The final review lists every stage's current evidence status and reason;
reaching it does not qualify placement. Refresh evidence after working in a
tool. The wizard and existing **Setup guide** report the same current evidence;
checklist marks remain operator notes.

**All setup tools** returns to the seven tabs, including Fine Registration,
Accuracy Validation and Coordinate Audit. Reopen **Setup wizard** to resume the
same guided stage. Navigation is held during active operations, while the
existing **STOP / LASER OFF** control remains available.

## Establish the error contributions

Use the precision assessment with an independent holdout pattern:

- Inspect native source sampling at the corners, edges and center. Rectified
  display pixel pitch is not the camera's native sampling or its physical accuracy.
- Establish the photo pose, then record ten captures without movement.
- Record ten additional Home / park-and-capture cycles. Each capture is an
  explicit operator action. Compare point-to-point variation between the two
  groups. Larger variation after Home / park suggests a positioning or restraint
  contribution; it does not by itself identify a particular mechanical fault.
- A completed baseline retains its recorded plane when a later workpiece is
  measured. A camera, lens, support map, mounting or datum change invalidates it.
  A new or different height model leaves the old baseline as diagnostic history;
  it cannot qualify that model automatically.
- Measure actual laser-center marks relative to independent intended locations
  across all nine XY regions. Record signed X and Y error, the measurement method
  and radial uncertainty. Do not use the same fitted camera map as the sole
  measuring instrument for its own accuracy claim.
- Keep geometry approximation separate: precision tracing retains the requested
  fit tolerance and reports when the source sampling cannot support that request.
  Native-detail analysis is bounded in memory; an oversized capture rejects
  instead of silently reducing its sampling.

Known curved geometry is flattened for toolpaths with a 0.025 mm approximation
envelope; large ellipses and rounded corners use enough segments to maintain
that envelope. Precision SVG parsing also accounts for transforms and final
placement scale. G-code coordinate rounding is a further, separate contribution.
Neither allowance replaces optical or physical qualification.

For a physical observation, the decision value is
`hypot(error_x_mm, error_y_mm) + uncertainty_mm`. Every observation must be at most
0.10 mm. Average error or RMS alone cannot satisfy this maximum-error target.
Measure laser-center placement separately from cut width, kerf, taper, charring,
and material shrinkage. Do not compensate those process effects by resizing a
known template without a separate explicit process decision.

## Height calibration

Begin a new named height calibration after establishing the reference and fixed
camera pose. For each plane, prepare the keyed calibration marks, run and capture
them through the existing guarded actions, review the complete detected pattern,
then save that slot. Record the measured signed elevation and its uncertainty.
Lower and upper evidence fit the model; a separate middle capture validates it.
Acquire the endpoint evidence across the intended interval, including spacers.

The camera model uses fixed lens intrinsics and one physical pose, with a plane
mapping `H(z) = K [r1 r2 t + z r3]`. Raw observations are undistorted once before
fitting. Runtime photographs compose the plane and inverse lens correction into
one image remap; raw clicks are lens-corrected once before the same plane mapping.
The support map, support bounds and machine coordinates remain fixed. The
support-plane fine-registration translation and residual mesh are not carried
onto another height.

Production selection requires schema-2 observations, unique lower/upper/middle
source identities, compatible camera/datum/pose bindings and numerical maximum
fit and holdout errors no greater than 0.10 mm. Passing these numerical checks
only enables use of the model for testing. It does not supply physical placement
qualification. Older schema-1 studies remain readable as diagnostics and need
fresh production evidence.

Height-error allowance is derived separately. Diagnostics report local
`XY change / height change`. Multiply this sensitivity by the probe-height error
to estimate its XY contribution. The displayed remaining-budget estimate uses
the larger fit/holdout optical residual, the 0.025 mm curve allowance and radial
coordinate rounding. It does not include unmeasured mechanical, localization or
metrology contributions. Do not interpret it as a certified
probe tolerance. Repeated measurements against an independently measured height
are required to establish probe error.

## Qualification and improvement decisions

Record independent positions at lower, intermediate and upper heights, including
repeated Home / park, after completing the one-height baseline. Publish the tested
rectangle, actual target coordinates, height interval, individual errors, maximum
radial error and uncertainty. Camera, lens, mounting, support mapping or datum
changes invalidate the corresponding saved evidence. A failed check keeps the
0.10 mm target and shows the failing contribution; it never increases the limit.

Prioritize improvements according to those measurements:

| Observation | Next setup check |
| --- | --- |
| Variation with no motion | Fixed illumination/exposure/focus, vibration, glare, feature sharpness and camera controls |
| Extra variation after Home / park | Camera/cable restraint, workpiece clamps, moving-bed play, repeatable park pose and settling |
| Stable XY bias across all heights | Independent XY reference, support map and tool-center reference |
| Bias growing with height or distance from center | Datum, probe error, spacers, plane parallelism and height-model evidence |
| Corners worse than center | Lens calibration coverage, source sampling, focus and camera mounting |
| Correct mark centers but wrong cut dimensions | Kerf, focus teaching, power/speed/material process and toolpath approximation |

Keep the present camera for this assessment. Any later hardware change should
answer a measured limitation and starts a new qualification. The software is
not a safety-rated control system.

## Persistence and implementation boundaries

- `AppContext` owns an immutable selected surface derived from a currently
  validated measurement. It includes the model, datum/reference identity,
  elevation, capture pose and controller session. Cached metadata never becomes
  motion authority.
- `.e3laser` schema 4 preserves schema 1–3 geometry on load and records optional
  surface provenance. Older E3 versions cannot open newly saved schema-4 files;
  existing atomic save, backup and autosave behavior remains in place.
- Model evidence, selection preference, setup evidence, project metadata and
  controller focus data are separate stores. No calibration photographs,
  generated marks or operator evidence are source fixtures.
- `E3SURFACE 1` is an application directive included in immutable job digests.
  Arm and Start revalidate the exact measured surface. It is consumed before
  serial output and cannot bypass bounded G-code validation or temporary arming.
- Desktop and browser use the same application mapping and job-binding methods.
  The guided setup interface is currently desktop-only.

Camera-model reference: OpenCV documents the intrinsic/extrinsic projection
model, lens distortion and pose fitting in its
[camera calibration documentation](https://docs.opencv.org/4.5.1/d9/d0c/group__calib3d.html).
These mathematical functions do not establish physical metrology accuracy.
