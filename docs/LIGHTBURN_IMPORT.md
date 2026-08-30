# LightBurn project import

E3 can import vector artwork and the usable operation-layer settings from both
current `.lbrn2` projects and legacy `.lbrn` projects through **File > Import
LightBurn project…**.

The importer is intentionally separate from machine execution. It translates
LightBurn data into ordinary E3 `SceneObject` and `OperationLayer` records; no
LightBurn-specific object type remains after import.

## Pre-import review

Selecting a project first runs the bounded LightBurn XML-structure scan without
vectorizing its shapes. A window-modal review shows source name, extension,
size, format and importer capabilities; referenced layers and object counts;
source and coordinate facts; warnings; approximations; unsupported features;
errors; and the SHA-256 of the exact scanned source bytes. Facts the format does
not report are shown explicitly rather than silently omitted. The complete
manifest is retained, while the dialog shows at most 200 layers and 200 entries
per repeated text section and reports the exact number omitted.

Errors or unsupported features block the project and disable **Import**. Valid
and warning-only manifests still require the operator to explicitly choose
**Import**. Cancel returns without changing project layers, objects, history,
selection, active layer, or the current drawing/point-pick authoring state.

After approval, the existing strict LightBurn loader reads the file again and
verifies those bytes have the reviewed SHA-256 before scanning or performing
the authoritative geometry conversion. A changed source or strict-parser
failure stops before any project, history, selection, or authoring-state change.
A successful import remains one undoable E3 command.

## Safety boundary

Every imported layer is created with **Output disabled**, regardless of the
state saved by LightBurn. Imported speed, power, pass count, raster interval,
scan angle, overscan, and air-assist values are untrusted data until the
operator reviews them for the connected E3 machine and explicitly enables that
layer. Importing a file does not arm the laser, generate G-code, move the
machine, or start a job.

An imported Air Assist value populates the ordinary Cuts / Layers **Air assist**
checkbox while the imported layer remains output-disabled. If the operator later
reviews and explicitly enables that layer, powered generation uses the active
saved machine's configured output. A requested powered layer is blocked when
that mapping is disabled or incompatible; import never chooses a controller
command.

The XML reader rejects document-type and entity declarations, malformed XML,
unsupported roots, invalid/non-finite numbers, excessive file/XML/shape/point
sizes, and geometry that cannot be translated without silently losing it.

## Imported content

The first native importer supports:

- rectangles and rounded rectangles;
- circles and ellipses;
- line and cubic-Bezier paths, including LightBurn's compact `LineClosed` and
  `LineOpen` encodings;
- nested groups, preserved E3 object grouping, and full six-value affine transforms;
- LightBurn vertex/primitive-list reuse through `VertID` and `PrimID`;
- text when the LightBurn file contains its vector `BackupPath`;
- referenced cut layers, familiar layer colors, layer order, Line/Fill/Raster
  mode, speed, maximum power, passes, line interval, scan angle, overscan, and
  air assist.

The complete imported layout is recentered as one unit at the current E3
project center. Relative position, scale, rotation, mirroring encoded by the
shape transforms, and layer assignment are retained. The import is one
undoable E3 command.

This importer still uses its established bounded polyline conversion in this
increment. Those polylines enter schema-3 projects through the compatibility
adapter and immediately become canonical native line-segment subpaths; no
legacy `geometry.polylines` copy is retained. Preserving LightBurn cubic
controls directly is follow-up work and does not require another project-schema
change.

## Deliberate first-version limits

E3 stops the import with a useful error instead of silently dropping:

- embedded LightBurn bitmap objects; export these from LightBurn as PNG and use
  E3's raster-image importer;
- live text that lacks a vector backup; convert the text to paths in LightBurn;
- LightBurn-only shape or primitive types that do not have a lossless E3
  representation.

LightBurn controller-specific parameters such as galvo frequency, pulse width,
PPI, Z-axis behavior, tabs/perforation, and wobble cannot be represented by
E3. The importer reports those settings for review. When a layer stores
separate minimum and maximum powers, E3 imports the maximum and reports the
loss of the minimum-power value. A combined Fill + Outline operation imports
as Fill and reports that a separate E3 line layer is needed for the outline.

Unused LightBurn cut settings that have no project objects are not added.

## Recommended verification

After import:

1. Leave all imported layers output-disabled.
2. Inspect the objects, dimensions, and layer assignments on the E3 canvas.
3. Review every imported operation's mode, speed, power, passes, interval,
   angle, overscan, and air-assist state.
4. Replace settings that came from a different laser type or power class.
5. Generate and inspect E3's exact Preview with output still disabled or at
   zero effective power.
6. Enable only the reviewed layers required for the job.

A LightBurn file made for a CO2 or fiber laser must never be treated as a safe
10 W diode profile merely because its numbers were parsed successfully.
