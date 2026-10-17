# Toolpath generation performance

The desktop Generate pipeline batches raster coordinate operations and bounds
checks, filters vector contour candidates before exact containment tests, and
releases encoding geometry before constructing the immutable exact preview.
Canonical linear G-code has a fully matched preview indexing path; other syntax
uses the existing general parser. Immutable move records retain their named fields
using tuple storage, with bounds reduced from those exact values. Raster run
clipping and serialized-coordinate collection use batch arrays. Unchanged
placement geometry and zero-offset controller geometry share read-only spans;
nontrivial transforms retain independent coordinates. A bounded cache reuses
coordinate formatting. Controller admission and execution validation
remain separate and unchanged.

The selected material-surface metadata is captured with the generation request
and inserted before exact indexing. The desktop still rejects a changed surface,
placement capture or request context. This avoids building the same large preview
twice just to insert a provenance line. Rendering uses the complete final plan;
no resolution reduction, path omission or preview sampling is introduced.

## Repeatable offline measurement

Run `scripts/benchmark_toolpath_generation.py` with the repository Python.
It reports snapshot/advisory preflight, generation, and total preparation time.
The total includes exact indexing, preview summary preparation and final asset
verification. It excludes GUI painting, upload, Start and controller execution.

```powershell
python scripts/benchmark_toolpath_generation.py --kind raster --bind-surface --iterations 3
python scripts/benchmark_toolpath_generation.py --kind vector --grid 56 --bind-surface --iterations 3
python scripts/benchmark_toolpath_generation.py --project 'C:\artwork\project.e3laser' --bind-surface --iterations 3 --output build/generation.json
```

`--source-root` can point at a baseline checkout for an equivalent comparison.
The baseline measurement reproduces the previous desktop surface binding and
second indexing pass when the older generator lacks the new metadata argument.
Add `--desktop-cancellation` to include the worker's cooperative cancellation
callbacks in both generation and preview preparation.
The deterministic raster is 180 x 180 mm at 0.2 mm pitch; vector contours fill a
192 mm grid. Timings are measurements, not CI timing assertions.

A supplied project is read without saving it; the script verifies its hash and
its generated raster identities. Its placement, travel envelope and optional
surface binding are explicitly synthetic. This is not a calibrated hardware
export or evidence of physically verified travel. No controller is constructed.

## Acceptance scope

The target is less than ten seconds for representative full-size artwork on the
operator's Windows workstation. Report the artwork size, pitch, move count,
Python version, source revision and repeated timings with each result. Complexity,
passes, contour nesting, power correction and machine speed affect that result;
it is not a universal deadline for every program below the command cap.

See `CURRENT_STATE.md` for measurements and the distinction between source,
offscreen, frozen-build, interactive and physical verification.

## Initial Windows source measurements, 2026-10-17

Python 3.14.4 on the operator workstation; synthetic offline authority, surface
binding enabled, no GUI painting or controller access:

| Workload | Moves | Baseline total | Optimized median | Optimized maximum |
| --- | ---: | ---: | ---: | ---: |
| Retained 162.901 x 154.308 mm emblem, 0.1 mm pitch | 726,664 | 42.735 s median, three runs | 9.071 s | 9.109 s |
| Dense 180 x 180 mm raster, 0.2 mm pitch | 811,800 | not measured with this exact final harness | 9.605 s | 9.731 s |
| 3,136 closed vector contours over 192 mm | 228,928 | 36.930 s, one run | 3.143 s | 3.175 s |

Every optimized result uses three runs. The matched emblem and vector programs
are byte-identical after removing only the generation timestamp. Keep installer,
interactive and physical verification separate from these source measurements.

## Follow-up Python 3.12 verification

The first packaged Python 3.12 raster result exceeded ten seconds, so follow-up
work reduced geometry and move-record allocations, reused parsed numeric words,
and batched additional raster operations. The final source with Python 3.12.10
and the hosted package's dependencies takes 8.596 / 9.536 / 9.601 seconds for the
811,800-move dense raster, including desktop cancellation callbacks, surface
binding, exact indexing and preview preparation. Its program hash remains
`c728dd6f301d4d8ad6ca0b93dc020867bb01bceaa2c812d08736f661277ecdae`.
These source/dependency checks precede the final archive verification below.

## Final archived-code measurements

Canonical hosted build 0.8.2 / `5aaf7f02fb5881367bbb06a30abc919a61f088db`
passes archive identity and bundled-Qt offscreen verification. Its archived code
and bundled dependencies, executed with isolated Windows CPython 3.12.10,
produce these three-run results with surface binding and desktop cancellation:

| Workload | Median preparation | Maximum preparation |
| --- | ---: | ---: |
| Retained 726,664-move emblem | 6.174 s | 6.190 s |
| Dense 811,800-move raster | 6.688 s | 6.834 s |
| 3,136 contours / 228,928 moves | 2.825 s | 2.848 s |

Command hashes match the comparison programs after removing only the timestamp.
The baseline table above uses Python 3.14 source; do not interpret cross-runtime
ratios as a controlled Python-version comparison. In a separate offscreen run,
emblem preparation takes 6.237 seconds and both complete preview canvases take
another 2.930 seconds: 9.168 seconds combined. This does not verify full EXE
startup, real interactive timing, upload, Start or physical execution. Synthetic
placement and travel authority remain the measurement boundary.

## Combined feature build verification

Build 0.8.19 / `8d01de7ff8e6f936495bdf7c99e3959f8f090b7c`, integrated into main
through PR61, retains the speedups alongside probe refresh, arc editing and linked
image/vector movement. Three runs of its exact archived code with Windows Python
3.12.10, bundled dependencies, surface binding and desktop cancellation produce:

| Workload | Median preparation | Maximum preparation |
| --- | ---: | ---: |
| Dense 811,800-move raster | 6.466 s | 6.760 s |
| 3,136 contours / 228,928 moves | 2.606 s | 2.663 s |

Both timestamp-normalized program hashes match the original speed build.
The same offline scope and physical-verification limits apply.
