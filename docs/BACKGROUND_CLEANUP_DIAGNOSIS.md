# Background cleanup diagnosis — 2026-10-15

The latest saved barrel project has background cleanup **disabled**. This is the
strongest available explanation for the unwanted background marks in its engraving
preview. The actual source and full generation/preview pipeline remove those marks
when cleanup is enabled at tolerance 5. No cleanup implementation defect was
reproduced. A saved file cannot establish an unsaved window's current checkbox or
whether an already generated preview predates the edit.

## Evidence and scope

The read-only source was `Vintage Barrel Aged 60 Years Emblem.png`, 1289 × 1221
pixels (1,573,869 total), SHA256
`4a8840ee43ab45da85e97d668d4e2c2d3d4b8739b222b9c61057731614950857`.
The latest inspected schema-5 autosave ends
`138e8d6a82b04835b587b305539666cd.autosave.e3laser`; its image contains
`background_cleanup = {enabled: false, tolerance: 5}`. It is 80 × 75.779674 mm,
centered at X95.5/Y95.5, rotation 0°, with 0.1 mm interval, 2000 mm/min, 10% power,
one pass and 2.5% overscan. The earlier enlarged reproduction autosave was schema 4
and had no cleanup settings.

Measurements used current feature source at
`aac45cdb58f1e746ecfd4c08624fd74230c172f4`, with an explicitly synthetic identity
191 mm frame and software-only work area. They used the low-level generator's
absent-envelope compatibility mode, which retains the old full-width scan behavior.
This is not a test of the frozen 0.7.206 executable or physical calibration/travel.
The cleanup algorithm and cleanup dialog source are unchanged from the selected
0.7.206 application revision `dcaa217a019bfab986af2bb28506264c830e7a24`.

Only an in-memory copy's cleanup enabled field changed for the comparison. Original
source and autosave hashes remained unchanged. No machine/controller connection,
settings change, job send or laser output occurred.

## Measured effect

Tolerance 5 includes grayscale 250–255 connected to an outside edge. It whitened
578,173 pixels (36.74%), exactly matching the earlier feature verification.
Another 1,131 eligible bright pixels were not edge-connected and remained intact,
as specified. The feature does not remove the barrel's internal wood texture.

| Full generated 80 mm job | Cleanup off | Cleanup on, tolerance 5 |
|---|---:|---:|
| Executable commands | 183,098 | 181,410 |
| Powered moves | 89,602 | 88,791 |
| Powered distance | 23,539.9 mm | 23,458.7 mm |
| Powered rows | 652 | 637 |
| Powered moves in the clear outer 1 mm border | 120 | 0 |
| Powered distance in that border | 12.0 mm | 0 mm |
| Positive power words, on a 1000-unit scale | 100 | 100 |

Powered bounds change from X55.5–134.8/Y57.65–132.85 to
X62.1–130.4/Y68.45–132.05. Both programs retained the same image placement and
source dimensions. The larger powered-distance total is dominated by intentional
artwork; it should not be mistaken for a background-removal percentage.

The actual `prepare_job_preview` and `JobPreviewCanvas` paths rendered both plans
with high contrast enabled and laser-off travel hidden. Visual inspection confirms
scattered dots across the exterior blank space before cleanup and clear exterior
space afterward. The barrel and ribbon texture remain. Diagnostic PNGs are ignored
artifacts under `build/cleanup-diagnosis/barrel-cleanup-false.png` and
`barrel-cleanup-true.png`, not committed image fixtures.

## UI and the separate overscan issue

The dialog correctly opens with the saved unchecked state and explicitly says
“Cleanup is off. The original image will be used.” Tolerance is disabled while
cleanup is off. Apply remains available, so opening Clean background and applying
without checking its checkbox can leave cleanup disabled. Enabling it in a discarded
offscreen review produced the correct “578,173 pixels whitened (36.74%)” summary.
No UI or default-setting behavior was changed during diagnosis.

Enable **Clean background**, inspect that nonzero summary and the After view,
then Apply and generate a fresh engraving preview once its normal preflight passes.
Changing tolerance alone while cleanup is disabled has no processing effect.
Trace image to vectors independently uses the original source; it is not the
raster engraving preview examined here.

Cleanup whitens pixels; it does not crop or resize the image. In the older full-width
scan behavior, rows with any powered content still traverse the rectangular image
width, then add overscan. In this compatibility comparison the movement X bounds
therefore remain 53.5–137.5 mm even after cleanup. That separate movement behavior
explains why cleanup alone could not cure the enlarged image's overscan rejection.
The new raster overscan implementation addresses movement footprint separately and
still requires verified travel configuration; cleanup grants no extra travel.

Existing tests already cover disabled behavior, application before minification,
rotated/mirrored equivalence to an explicitly whitened source, dithering, source
integrity, save/undo, and variant-specific preview caching. No duplicate regression
or application change was added. Parent verification of cleanup and the existing
processing-duplication regressions reported 35 passing tests in 2.22 seconds.
