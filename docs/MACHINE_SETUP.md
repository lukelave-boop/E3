# Native machine setup

Open **Tools > Machine Setup…** in the desktop application. The Camera panel's
**Calibrate lens…** and **Bed alignment…** buttons open the same window at the
relevant step. The dialog uses the shared `AppContext`; it does not start a web
server or create a second camera owner.

## 1. Camera

- Inspect a raw preview and save a corrected still.
- Apply every camera control from the active configuration. Unsupported or
  rejected V4L2 controls are reported rather than silently treated as applied.
- In simulation, select the perspective-bed, checkerboard, or workpiece scene.

If another application has exclusive camera access, the desktop presents one
acknowledgeable **Camera unavailable** message. Repeated live-refresh failures
remain in the status display and do not open more dialogs or steal focus.
Automatic refresh continues quietly so recovery can be detected. After closing
the other application, use **Refresh camera** for an explicit retry. If the
camera is disconnected, frame-less, or reporting a read error, that action
releases the failed capture and reopens the configured device before requesting
a new frame. A successful frame clears the fault latch and reports recovery.

Mount the camera rigidly, independently of the moving bed and gantry. Changing
the mount, resolution, or focus invalidates alignment evidence and requires the
lens and bed calibrations to be checked again.

## 2. Lens

Print `targets/checkerboard_9x6_20mm.svg` at 100% scale and verify its square
size. Capture at least the configured minimum number of usable views with the
complete flat board at the image center, corners, and edges and at several
modest tilts. Solve only after the usable count reaches the minimum. Review the
RMS and mean reprojection errors as well as image coverage.

Clearing the solved model retains captured checkerboard images. It does not
clear the bed mapping automatically; redo the bed mapping after changing the
lens model.

## 3. Bed mapping

Keep the laser incapable of emission. Put the machine at its repeatable
photography pose, capture a fixed bed image, then click each mark center and
enter the exact machine X/Y that produced it. Four point pairs are the minimum;
nine or more well-distributed points are preferred.

A coordinate CSV may use `x_mm`/`y_mm`, `machine_x`/`machine_y`, or `x`/`y`
headers, with an optional `fiducial`, `index`, `id`, or `label` column. It loads
the target coordinates in sequence; the operator still clicks each observed
pixel. The 5×5 cross-grid detector is also available when a rough existing map
can predict search regions.

Solve and review RMS error, maximum error, and inlier count. A planar mapping
is valid only at the calibrated material height and repeatable bed pose.

A symmetric cross grid can produce a low residual even when its machine X or Y
labels are reversed. With the laser incapable of emission, verify that positive
controller motion goes toward the positive workspace direction shown by the
camera. If an existing map is proven mirrored, change **X mapping** or
**Y mapping** from NORMAL/OFF to REVERSED/ON and review the re-solved transform.
It must not be used to compensate for a loose workpiece, changed camera mount,
or non-repeatable bed pose.

The native controls now show explicit **NORMAL (OFF)** or **REVERSED (ON)**
states. These states are stored with the bed calibration and restored whenever
Machine Setup or the application reopens. Maps created before this metadata was
introduced show an inferred state and a separate confirmation button. Confirming
that display records the state without changing points; changing either toggle
mirrors the saved labels and re-solves the map. Perform the laser-off direction
check before confirming or changing either state.

Machine Setup also remembers its window geometry, selected tab, simulation
scene, cross sizes, and marking speeds in the active application data directory.
Verified marking power deliberately returns to `0%` on every open so a previous
powered calibration value is never silently carried into another session.

On a bed-slinger, rigidly restrain the calibration surface or workpiece to the
moving bed. Motion of the surface relative to the bed invalidates both the
mapping and any alignment comparison made from its camera image.

## 4. Fine registration

Fine registration verifies the solved bed map using eight fresh crosses placed
between the common 5×5 grid locations. Use a clean sacrificial surface at the
calibrated material height and rigidly restrain it to the moving bed.

1. Set the cross size and marking speed. Leave marking power at zero.
2. Choose **Prepare dry registration path**. The dialog closes and loads the
   validated path into the normal G-code preview. Run and observe that dry path.
3. Reopen this tab, enter a visible-marking power that has already been verified
   for the material, and choose **Prepare powered mark job**. Review and run it
   through the normal powered-job confirmation and temporary arming path.
4. Reopen the tab and choose **Home / park, capture and analyze marks**.
5. Review every commanded coordinate, observed coordinate, and X/Y residual.

The **Use** checkbox permits at most two clearly obstructed, damaged, or
incorrectly detected crosses to be excluded. A detection beyond the bounded
search/residual limits is suggested as excluded automatically and remains
visible in the table for review. Unchecking ordinary points merely to obtain a
preferred answer is not a valid calibration. At least six of the eight marks
must remain.

The application classifies a consistent residual as one global translation and
offers **Apply reviewed translation** only when the centered scatter is small,
every mark is within the detection limit, and the cumulative correction is no
more than 5 mm. A changing residual is reported as position-dependent and
cannot be applied as a translation.

The tab also reports an independent **Full-bed fit**. **Apply reviewed full-bed
map** is enabled only with at least seven RANSAC inliers, broad bed coverage,
sub-millimetre fit residuals, bounded full-bed movement, and no orientation flip
or excessive local scale. The full map is confirmation-gated and never inferred
from only six accepted marks. **Reset full-bed refinement** restores the exact
solved map retained before application. Reset an applied fine translation before
applying or resetting a full-bed refinement.

If neither correction passes, check bed/workpiece restraint, camera rigidity,
surface height, and redo the full bed mapping. **Reset fine translation** removes
only the bounded translation. Solving or reversing the base bed map clears all
fine registration.

The fine translation belongs to camera-to-machine registration. It is not a
laser-head mounting offset and does not modify `laser.spot_offset_x_mm` or
`laser.spot_offset_y_mm`.

## 5. Accuracy validation

This is the independent holdout check for a translation or full-bed refinement.
It uses five locations that are not among the eight fine-registration marks and
does not fit or change calibration.

1. Put a clean sacrificial surface at the calibrated height and rigidly restrain
   it to the moving bed.
2. Leave power at zero and choose **Prepare dry validation path**. Run and
   inspect the path through the normal job controls.
3. Reopen the tab, enter a previously verified visible-marking power, choose
   **Prepare powered validation job**, and run it through the normal confirmation
   and temporary arming path.
4. Reopen the tab and choose **Home / park, capture and score holdouts**.

All five crosses are required. The program reports each commanded and observed
coordinate, X/Y error, total error, RMS error, maximum error, and mean bias. A
**PASS** requires RMS error no greater than `0.5 mm`, maximum error no greater
than `1.0 mm`, and confident detection of every mark. Low-confidence, incomplete,
dry-only, or stale-map sessions are rejected rather than scored. Validation
never applies a correction; a failure means the camera map is not physically
verified.

The tab also retains rectangular-workpiece and ArUco-fiducial detection as
secondary camera diagnostics. Those detectors are not accuracy proof.

## Browser parity

The desktop is the primary operator UI. It now exposes the browser's camera
controls, still capture, synthetic scenes, lens calibration, manual/CSV/5×5
bed mapping, workpiece detection, fiducial detection, SVG import/placement,
G-code generation/export, dry framing, controller connection, diagnostics,
camera-pose parking, guarded execution, software stop, fine registration, and
independent holdout accuracy validation. The browser remains a legacy
single-SVG alternative, not a required setup surface.

For serial-hardware jobs, desktop **Start** performs `M5`, Home, camera-pose
parking, and an idle wait before arming and streaming the job. A failed
preflight blocks the run. This removes the need to press **Home / park**
manually before every job; it does not replace the operator's laser-off dry
frame and origin/direction checks.
