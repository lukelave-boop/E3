# Cutting templates and camera alignment

Cutting templates are reusable descriptions of a label sheet's cut geometry.
They are separate from `.e3laser` project files: a project is an editable job,
while an `.e3template` file is a library item that can be identified, aligned,
reviewed, and instantiated into a project.

## Creating a regular grid template

Choose **Create > Design grid cutting template** or **New grid…** in the
Templates panel. The dedicated designer provides a live preview and editable:

- template name and description;
- row and column counts;
- cut width, cut height, and corner radius;
- horizontal and vertical spacing, entered either as an edge gap or a
  center-to-center pitch.

An edge gap is the clear distance between adjacent cut outlines. Center pitch
is the distance between their centers. The two forms describe the same layout:

```text
pitch = cut size + edge gap
footprint = cut size + (count - 1) × pitch
```

Changing spacing modes preserves the physical layout. Pitch cannot be smaller
than the corresponding cut dimension, and the corner radius cannot exceed half
the smaller cut dimension. The status card reports cut count, footprint, pitch,
and gap while the preview updates.

A grid is limited to 500 cut objects. The designer also blocks saving or adding
a grid whose footprint exceeds the current project work area. A one- or two-cut
grid can still be saved and positioned manually, but it cannot satisfy the
automatic matcher's minimum of three features.

Choose **Save template** to add the design to the reusable library, or **Add to
project** to create editable rounded rectangles at the center of the
current project. Adding a grid is one undoable project operation. Templates
created by this designer retain their editable grid recipe, so **Edit grid…**
can later change their dimensions, radius, rows, columns, and spacing without
changing the template's persistent identity. Templates created from arbitrary
project geometry do not claim to be editable grids.

If saving or insertion fails, the designer remains open and keeps every entered
value so the problem can be corrected and the action retried.

## Creating a template from project geometry

For freeform or mixed geometry:

1. Build the exact cut geometry for one label-sheet layout in a project.
2. Select a single rectangle and use the **Transform** panel to edit its width,
   height, and corner radius. Radius is limited to half the smaller dimension.
3. Hide or disable any objects that must not be part of the reusable cut set.
4. Choose **From current project…** in the Templates panel and provide a name.

Width, height, and radius edits to an ordinary rectangle are applied together
as one undoable shape edit. Project-authored templates preserve the resulting
geometry, but they do not contain a parameter-grid recipe and therefore cannot
be opened with **Edit grid…**.

## Alignment workflow

1. Place a printed sheet under the corrected camera.
2. Either select the template from the library or ask the application to rank
   the library against the detected label geometry.
3. Review the proposed center, rotation, match confidence, residual error, and
   warnings over the frozen camera image used for that exact match.
4. Adjust the placement when necessary. Drag any cyan cut to move the entire
   template, drag the round handle to rotate it about its reviewed center, or
   use the Center X, Center Y, Rotation, and nudge controls. Canvas gestures and
   numeric values stay synchronized. Solid cyan lines are the aligned cut
   template; dashed amber camera detections remain fixed so the correction can
   be compared against the captured sheet. The on-canvas key identifies every
   transient overlay currently visible.
5. Choose **Create aligned cut objects**. All template objects are added to the
   active project layer as one undoable batch.

Manual template selection and placement remain available when automatic
identification is ambiguous. Sheets with the same visible geometry cannot be
reliably distinguished by geometry alone.

Automatic matching compares feature centers, width, height, and orientation;
it does not compare rounded-rectangle corner radius. Two templates that differ
only in corner radius are therefore indistinguishable to automatic matching.
Select the intended template manually and review its cut overlay in that case.

## Testing alignment without hardware

The Templates panel exposes a **Test image** section only in safe simulation.
It replaces the simulated camera's corrected workspace frame temporarily; it
does not change project objects, template files, machine settings, or hardware
state.

Two test sources are available:

- **Load test image…** accepts a PNG or JPEG that already represents a
  top-down, corrected view of the complete configured work area. The left and
  right edges correspond to `x_min` and `x_max`; the top and bottom correspond
  to `y_max` and `y_min`. Its integer dimensions must be consistent with the
  full work area at one uniform pixel scale; only the half-pixel uncertainty
  caused by rounding image dimensions is accepted. The loader resizes it to
  `work_area.width × pixels_per_mm` by
  `work_area.height × pixels_per_mm`, but does not undistort, rectify, crop,
  infer bed boundaries, or assign physical calibration to an ordinary photo.
- **Generate from selected template…** creates a deterministic full-bed image
  from the selected template's matching features and trace options. Enter the
  known Center X, Center Y, and Rotation, optionally add pixel-noise strength,
  and choose how many labels to omit. The same settings produce the same image,
  making the matcher's reported pose directly comparable with the known input.
  Generation is rejected if the requested pose clips a visible label outside
  the configured work area. Leaving fewer than three visible features cannot
  satisfy automatic matching, although manual placement remains available.

After loading or generating an image, its status reads **TEST IMAGE · FROZEN**.
Run **Auto identify and align** to rank the library, or choose a template and run
**Align selected template**. Detection, matching, acceptance gates, overlay
review, direct canvas adjustment, and object creation use the normal desktop
workflow. The frozen source prevents a later simulated camera frame from
replacing the image during review. Choose **Return to synthetic camera** to
discard the temporary override and resume the ordinary simulated feed.
Starting a new project or opening another project also ends the temporary test
image session so a full-bed image cannot retain an obsolete canvas transform.

This workflow can expose software regressions in trace settings, template
ranking, rigid-pose recovery, missing-label inference, and review controls. A
generated image is derived from the same ideal geometry that the matcher is
asked to recover, and a loaded image is only as physically meaningful as its
prior correction. Neither is evidence that a real lens model, bed homography,
camera pose, material height, parallax, controller, motion system, or powered
laser is correct. Physical validation still requires a recorded real-camera
and no-laser dry-frame procedure.

## Template format

Templates are versioned JSON files with the `.e3template` extension. The
desktop stores them under the configured application data directory's
`templates` folder. Library writes are atomic, use portable safe filenames,
and do not overwrite an existing file unless replacement is explicit. A
resilient catalog scan keeps valid unique templates usable if another file is
malformed; it reports bad files and excludes every entry in a duplicate-ID set.

Schema version 1 stores:

- a persistent template ID, name, and description;
- local bounds and physical width/height in millimetres;
- cloned `SceneObject` cut geometry;
- matching features containing local center, width, height, and rotation;
- the tracing options captured when the template was created;
- an optional `marker_id` value;
- creation/modification timestamps and extensible metadata.

Grid-authored templates use that extensible metadata for a versioned
`rectangle_grid` authoring recipe. The stored recipe contains the name,
description, rows, columns, cut dimensions, corner radius, and edge gaps.
Center pitch and footprint are derived rather than persisted as competing
values. Editing rebuilds normalized objects and matching features while
preserving the template ID, creation timestamp, and surviving cell identities.

Only visible, output-enabled project objects are copied. Their positions are
normalized around the center of their combined bounds. Original project
objects are not mutated, and applying a template creates new object IDs and
assigns every new object to the active layer.

For imported SVG geometry, independent closed outer contours stored in one
compound path become separate matching features. Contours contained inside an
outer contour are treated as holes and are not mistaken for additional labels.

The optional `marker_id` is schema metadata only. There is no QR, ArUco, or
other marker detector connected to template identification yet. A future
detector can use this field without changing the version-1 geometry model.

## Matching and placement contract

Automatic identification compares unordered detected features with each
template's feature geometry. It considers centers, dimensions, orientation,
direct versus inferred detections, coverage, and residual error. Similar
scores are reported as ambiguous rather than silently selecting a winner.
Every candidate and trace-option group is evaluated against the same captured
corrected frame. Camera delivery stays frozen while an accepted overlay is
reviewed, so the displayed image and proposed placement cannot drift apart.

The accepted placement is always rigid:

```text
template-local point
  -> rotate by the reviewed angle
  -> translate to the reviewed machine-coordinate center
  -> create project object
```

Width, height, local geometry, and relative spacing are preserved. Observed
scale differences are diagnostic warnings and are never applied to cut
geometry. Unexpected scale can indicate incorrect calibration, parallax,
camera pose, material height, or the wrong template and must be reviewed.
Direct canvas editing is also a rigid placement operation: it cannot resize an
individual cut or change spacing inside the template. Once the reviewed cuts
are created in the project, they become ordinary project objects rather than a
persistent template group.

Automatic acceptance currently requires at least three matched features, two
direct detections, 50% template coverage, 55% confidence, no more than 1.0 mm
RMS residual or 2.0 mm worst-point residual, and no more than 3.5% reported
center-spacing or feature-dimension scale mismatch. An unresolved
look-alike-template choice or directional
half-turn pose also prevents automatic acceptance. These are provisional
software gates backed by synthetic tests; they are not measured physical
accuracy limits.

Automatic matching requires rectified, machine-coordinate detections from a
valid bed mapping. Manual placement does not prove that the camera calibration
or physical sheet position is correct.

Any project edit after toolpath generation invalidates the generated G-code and
preview. Generate a new dry frame or job after applying or adjusting template
objects; stale generated output is refused before machine access.

## Verification boundary

The template model, versioned round trip, atomic and resilient library behavior,
normalization, compound-path features, rigid instantiation, malformed-schema
rejection, and geometric alignment/ranking have synthetic automated coverage.
The controller, widgets, frozen-frame review, transient overlay, direct-canvas
drag/rotation, object application/undo, cancellation, and stale-job guards have
offscreen behavioral coverage. An actual offscreen desktop window also completed a template
save/reload/apply/undo smoke test.

The rectangle-grid builder, editable authoring metadata, exact-ID library
replacement, work-area/object-count rejection, dedicated designer controls,
live preview calculations, and ordinary rectangle size/radius editing also
have focused model, history, and offscreen-widget coverage. This is automated
software evidence, not an interactive usability or physical alignment result.

The safe-simulation test-image path has automated coverage for full-bed image
validation and resizing, deterministic known-pose generation, color and
contrast tracing, frozen-frame activation, restoration of the synthetic camera,
and desktop control wiring. This remains synthetic software evidence.

It has not yet been verified with a real corrected C920 image, a physically
measured label sheet, controller motion, or powered laser output. Marker-based
identification is not implemented. Real-camera validation must record the
template, calibration, material height, detections, residuals, manual
correction, and repeatability before any accuracy claim is made.

Always inspect the overlay and run a no-laser dry frame before a real job. The
template matcher and manual correction controls are positioning aids, not
safety-rated controls.
