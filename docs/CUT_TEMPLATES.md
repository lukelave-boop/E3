# Cutting templates and camera alignment

Cutting templates are reusable descriptions of a label sheet's cut geometry.
They are separate from `.e3laser` project files: a project is an editable job,
while an `.e3template` file is a library item that can be identified, aligned,
reviewed, and instantiated into a project.

## Intended desktop workflow

1. Build the exact cut geometry for one label-sheet layout in a project.
2. Hide or disable any objects that must not be part of the reusable cut set.
3. Choose **Save project as cutting template** and provide a name.
4. Place a printed sheet under the corrected camera.
5. Either select the template from the library or ask the application to rank
   the library against the detected label geometry.
6. Review the proposed center, rotation, match confidence, residual error, and
   warnings over the frozen camera image used for that exact match.
7. Nudge the center or rotation when necessary.
8. Choose **Create aligned cut objects**. All template objects are added to the
   active project layer as one undoable batch.

Manual template selection and placement remain available when automatic
identification is ambiguous. Sheets with the same visible geometry cannot be
reliably distinguished by geometry alone.

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
The controller, widgets, frozen-frame review, transient overlay, object
application/undo, cancellation, and stale-job guards have offscreen behavioral
coverage. An actual offscreen desktop window also completed a template
save/reload/apply/undo smoke test.

It has not yet been verified with a real corrected C920 image, a physically
measured label sheet, controller motion, or powered laser output. Marker-based
identification is not implemented. Real-camera validation must record the
template, calibration, material height, detections, residuals, manual
correction, and repeatability before any accuracy claim is made.

Always inspect the overlay and run a no-laser dry frame before a real job. The
template matcher and manual correction controls are positioning aids, not
safety-rated controls.
