# Engraving image editor

Select one unlocked image and choose **Objects → Image → Edit image for engraving…**.
The editor changes a reversible mask and editable outline. It retains the original
asset, full source dimensions and physical image frame. Retained grayscale values
are unchanged unless **Invert retained artwork** is selected.

## Remove a barrel's shadow

1. Choose **Rectangle selection**, **Ellipse selection**, **Lasso selection**, or
   **Exclude brush** in the editing tools. Select **Subtract / exclude selection**
   for selections and mark the unwanted shadow. Adjust brush diameter in source
   pixels. Release completes one undoable gesture.
2. Use **Restore brush** or **Add / restore original pixels** to recover original
   artwork if you remove too much. Restore can recover pixels outside the outline
   and pixels excluded by legacy Clean background. Fully transparent source pixels
   always remain excluded, even after Restore and inversion.
3. Check **Before / original** to compare. Scroll to zoom; middle-drag or the Pan
   tool pans. **Fit** and **100%** reset the view. Cyan anchors and handles identify
   editable geometry; the brush cursor shows its footprint.
4. Choose **Apply to project**. Cancel discards the draft. The entire dialog is one
   project undo step. Generate and review a fresh job after applying edits.

The automatic threshold cannot tell a subject from a cast shadow. Removing a
separate shadow locally preserves the grayscale grain and lettering in the subject.

## Create and refine an outline

Set **background tolerance**, **detail**, **smoothing**, **minimum component area**,
and optional **interior holes**, then choose **Preview automatic trace**.
Tolerance is distance from white: higher values exclude darker background pixels.
Detail is fitting tolerance in source pixels; smaller values retain finer detail.
Smoothing can change boundaries. Minimum component area removes small disconnected
components. The trace supports multiple disconnected pieces.

The gold dashed outline is an unapplied candidate. **Apply preview outline** or
**Replace outline with preview** explicitly accepts it. Changing settings or
running another preview never overwrites accepted manual refinements.

Use **Polygon outline** to click straight anchors, or **Curved pen outline** to click-drag
tangents. Enter closes a contour. Additional contours retain existing ones.
**Edit nodes** lets you drag anchors and cubic handles. **Add node** splits the
following segment; **Delete node** removes the selected anchor. Remove a selected
contour to discard a separate unwanted piece. Invalid geometry is reported and
cannot be used for a mask or cutting vector; Undo restores the previous state.

Selections and brushes are saved as ordered reversible regions. To turn their
result into a new editable outline, use **Preview outline from edited mask**,
inspect it, and explicitly replace the outline. This clears the baked-in region
list in that new draft and is undoable. It can approximate boundaries according
to the selected detail, smoothing, component and hole settings. Existing manual
curves are preserved until that explicit replacement.

**Use outline as mask** can disable the outline mask without deleting its nodes.
Local exclusion regions remain active. **Reset edits** clears editor edits;
Undo restores them. Legacy Clean background remains a separate reversible setting.

## Create an aligned cutting path

Choose **Create aligned vector (output off)**. If there are local regions, first
review and accept a new outline from the edited mask. Choose optional interior
contours and an offset in millimetres: positive expands foreground; negative
contracts it. Preview the vector and inspect the green dashed result before
applying to the project.

The new PATH is on a new visible LINE layer with **Output off and power 0**.
Assign it to a reviewed operation and set power/output explicitly before it can
be cut. Creating an outline never starts a job or changes machine authority.

**Link image and vector transforms** defaults on. Moving, rotating, mirroring or
resizing the source image moves both together. The vector shape is a frozen
snapshot: later image edits do not change an already reviewed cut path. Select
the path and use **Detach outline from image** in Objects before independent
transforms. Deleting the source automatically detaches retained paths; Undo
restores the link. Duplicating both remaps the link; duplicating only a path
creates a detached copy.

Offsets are calculated at the current physical size. A later resize scales that
frozen geometry and its offset with the image; it does not recompute an approved
path. Generate a new vector explicitly if a new constant-mm offset is required.
Zero offset preserves exact native cubic segments. Nonzero offsets use mitered
polylines, flattening curves within 0.01 mm (or a smaller offset-dependent
tolerance). Open contours, duplicate segments, self-intersections, touching
contours, collapsed holes, excessive miters and topology-changing offsets are
rejected. Narrow notches and protruding detail can make even a modest offset
invalid; reduce the offset or explicitly refine/retrace and review the outline.
E3 does not silently merge, trim or repair them.

## Persistence, output and limits

Project schema **6** stores source identity, outline nodes/handles, mask regions,
trace settings, inversion, and vector links. Schemas 1–5 migrate without edits;
older E3 builds reject schema 6. Atomic save, backups and autosave remain in the
existing project pipeline. A changed source identity blocks use of saved edits.

The editor, canvas and desktop raster planner use the same portable processing
function. The generated-job Preview and G-code export use that generated plan.
Exclusions are a separate eligibility mask, reapplied after dithering. Output
requires the full source footprint of each raster cell to be eligible. This is
deliberately conservative at rotated or minified boundaries and may leave a
small unengraved edge, including a 0.001 mm serialization margin. It never restores excluded pixels through inversion or
resampling. Source pixel boundaries have approximately half-pixel sampling
uncertainty before the selected fitting/smoothing tolerance; no physical contour
accuracy is claimed without operator validation.

Source decoding is once per editor session. Mouse movements draw lightweight
overlays; full image processing occurs on gesture commit, not each move. The
accepted outline mask is cached. Trace workers use cancellation and generation
checks so stale results cannot replace newer edits. Edits invalidate prepared
jobs through normal history. No full toolpath is generated during editing.

This first stage implements local cleanup, automatic and manual outlines,
native node/handle editing, mask persistence, linked vector snapshots, offsets,
comparison and inversion. Magic wand/background selection, brightness/contrast,
levels/gamma, threshold adjustment of retained artwork, crop/straighten/flip,
sharpening/despeckle, tonal curves, and clone/heal are deferred. The browser's
single-SVG workflow remains separate. No machine settings, calibration, Pi
installation, motion or laser operation is part of this editor workflow.
