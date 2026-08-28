# G-code project import

E3 can import existing 2-D laser G-code through **File > Import G-code…**. The
importer accepts `.gc`, `.gcode`, `.nc`, and `.tap` text files.

## Pre-import review

Selecting a file first runs the bounded G-code scan without reconstructing E3
geometry. A window-modal review shows the source name, extension and size,
declared importer capabilities, reconstructed operation combinations, source
statistics, coordinate-mode facts, warnings, approximations, unsupported
features, errors, and the SHA-256 of the exact scanned file bytes. The complete
manifest is retained, while the dialog shows at most 200 operations and 200
entries per repeated text section and reports the exact number omitted.

Errors or unsupported features block the file and disable **Import**. A valid
manifest—including one containing only warnings or approximations—does not
start import by itself: the operator must explicitly choose **Import**. Cancel
returns without changing project layers, objects, history, selection, active
layer, or the current drawing/point-pick authoring state.

After approval, the existing strict loader reads the file again and verifies
those bytes have the reviewed SHA-256 before scanning or performing the
authoritative translation. A changed source or strict-translation failure stops
before any project, history, selection, or authoring-state change. A successful
result is added through one undoable project command, as before.

## Safety boundary

Imported G-code is **never sent directly to the controller**. E3 interprets a
bounded 2-D subset, converts powered XY motion into ordinary editable project
paths, and reconstructs operation layers from the modal feed (`F`) and laser
power (`S`) values. Every imported layer is created with **Output disabled**.

The foreign program's setup, travel commands, dwell timing, machine-coordinate
state, and end commands are not retained for execution. If the operator later
chooses to run the design, E3 generates a new program through its normal guarded
toolpath pipeline and requires the ordinary exact Preview and START JOB flow.

Imported speed and power are untrusted process data. A successful parse does not
mean those values are appropriate for the connected laser, material, focus, or
machine.

## Supported first-version program subset

The importer understands:

- `G0`, `G1`, `G2`, and `G3` XY motion;
- modal motion lines that omit a repeated `G` word;
- `G17` XY-plane arcs using either I/J center offsets or an `R` radius;
- `G20` inch and `G21` millimetre units;
- `G90` absolute and `G91` incremental coordinates;
- `G94` feed-per-minute mode;
- `M3`, `M4`, and `M5` laser state;
- modal `F` feed and `S` power values;
- common `M2` / `M30` program-end commands;
- `G4` dwell and `M7` / `M8` / `M9` coolant/air commands as review warnings,
  not as executable imported behavior.

`G54`-`G59` selection can be present, but the selected machine work offset is not
retained because the imported design is recentered into the current E3 project.
The importer reports that loss for review.

The importer rejects commands whose geometric meaning cannot be represented
without guessing, including Z/rotary-axis motion, `G92`, probing, machine-
coordinate moves, canned cycles, unsupported arc planes, checksummed blocks, and
unknown G/M codes.

## Layer reconstruction

Powered moves with the same effective feed, raw `S` value, and `M3`/`M4` mode
are grouped into one E3 Line layer. Travel moves split paths but do not become
cut geometry.

The reconstructed polylines pass through the schema-3 compatibility adapter
and become canonical native line-segment subpaths immediately. Foreign G-code
arc sampling is unchanged in this phase; the project does not retain a second
legacy polyline copy.

E3 looks for a commented S-value maximum such as `S-value max: 1000` or
`$30=1000`. When no authoritative maximum is present, it uses a conservative
scale inference (1, 100, 255, 1000, or the observed maximum) and displays a
review warning. This inference is only an editing convenience: output remains
disabled until the operator reviews the imported operation.

## Recommended verification

After import:

1. Inspect the complete shape and dimensions on the E3 canvas.
2. Review each reconstructed layer's speed and power.
3. Confirm any reported S-scale inference against the source machine/software.
4. Leave output disabled while checking placement and generated bounds.
5. Enable only layers that have been deliberately reviewed for the connected
   machine and material.
6. Generate a fresh E3 program and inspect its exact Preview before START JOB.

Import is a design-recovery/conversion feature, not a way to bypass E3's normal
machine safety and execution guards.
