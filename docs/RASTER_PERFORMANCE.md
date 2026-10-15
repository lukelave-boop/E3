# Raster pipeline performance review — 2026-10-15

This review covers the desktop project pipeline through offline Pi preparation.
It does not cover the browser SVG pipeline, network throughput, interactive
painting latency, controller throughput, acceleration, or physical engraving.
No controller was created or connected; no RPC, arming, START, or motion was
performed. The benchmark replaces those entry points with failing stubs.
The execution-time check from the actual homed carriage position into the first
job move is therefore outside these timings; the offline static boundary checks
and committed-file/current-policy recheck are measured.

## Measured baseline

Baseline: clean `52f25b4f7dec67f8ecfb6b69d7b39e627edc8573`, the active
background-cleanup feature checkout. Windows 11 build 26300, Python 3.14.4,
three runs per case, median elapsed time, no profiler during timing samples.
Call counts use a separate profiled run. Results are development-machine
observations, not performance guarantees or Pi measurements.

All cases use a deterministic 362 × 364 grayscale PNG, 180.752 × 182.367 mm,
0.5 mm sampling, 1,200 mm/min and 2.5% overscan per end. Their center is
X110/Y110 inside a synthetic 220 mm machine workspace so this performance
measurement is independent of the reported surface-boundary failure.
The white-border image has a black center; the near-white-border variant
replaces background 255 with 250, with cleanup disabled. The dense image is
uniform gray 128 and exercises ordered dithering.

| Baseline phase (ms) | White border | Near-white border | Gray dither |
| --- | ---: | ---: | ---: |
| Executable commands | 2,246 | 4,972 | 133,961 |
| Canonical bytes | 46,140 | 108,445 | 3,217,143 |
| Input manifest | 6.350 | 6.803 | 6.276 |
| Canvas image decode | 5.045 | 6.582 | 5.307 |
| Document snapshot | 0.164 | 0.205 | 0.220 |
| Structured preflight | 0.554 | 0.588 | 0.599 |
| Generation, cold cache | 81.254 | 146.475 | 3,304.659 |
| Explicit identical regeneration, warm cache | 79.556 | 143.594 | 3,441.886 |
| Exact preview index | 1.009 | 2.478 | 84.256 |
| Offscreen preview geometry construction | 4.471 | 6.037 | 197.057 |
| Source identity verification | 4.939 | 5.071 | 4.906 |
| Atomic export write | 4.008 | 4.145 | 8.881 |
| One local send preflight | 10.983 | 23.217 | 589.126 |
| Two identical local preflights | 21.411 | 46.771 | 1,205.822 |
| Cached immutable-proof integrity | 0.095 | 0.183 | 4.623 |
| Offline durable chunk upload | 21.560 | 38.900 | 922.726 |
| Offline Pi finalize | 48.545 | 56.017 | 641.311 |
| Committed-file/hash/current-policy recheck | 10.081 | 10.213 | 27.467 |

The phases are individually measured. Do not sum the table as a normal user
operation: explicit regeneration, double preflight and the historical comparison
are extra experiments, and UI/backend work can overlap in the application.
Preview geometry construction is offscreen Qt work, not an interactive frame
rate measurement. Upload uses Windows local durable storage without TCP.

## Ranked findings before additional optimization

1. **Generation dominates the dense case: 3.305 seconds.** An identical explicit
   regeneration costs 3.442 seconds. The vector planning cache does not cache
   raster sampling/dithering or emitted raster rows. Across the two generations,
   the profiler recorded two source decodes, two `_image_raster_rows` calls,
   730 dither-row calls and two `build_job_plan` calls. That is one decode and
   one plan build per generation, not an accidental second pass within a
   generation. Caching immutable raster processing could help repeated Generate
   after unrelated edits, but requires complete keys for source hash, cleanup,
   sampling, transform, layer settings and coordinate/safety authority. No such
   cache was added in this change.

2. **Remote local preflight still repeats unchanged work.** Code inspection and
   a forbidden-RPC experiment show `RemoteMachineService.preflight_program`
   calls `_policy.preflight_program` twice for identical bytes, on both powered
   and unpowered examples when no focus binding changes the text. The measured
   equivalent double pass for the dense job is 1.206 seconds versus 0.589 seconds
   for one pass. Avoiding the redundant pass is a follow-up candidate, not a
   completed fix here. Changed focus-binding bytes must still be validated;
   current focus, policy and operation-focus checks must remain authoritative.

3. **Durable preparation is material: upload 0.923 seconds and finalize
   0.641 seconds.** `PiJobStore._validate_artifact` first hashes the file, then
   rereads it for UTF-8 validation; committed-file recheck hashes/reads again and
   compares canonical bytes. The full benchmark observed two file-hash calls,
   one text read and one committed-byte read. Those observations establish
   repeated I/O, but do not establish that redundant reads dominate the measured
   time: upload includes many durable chunk/metadata writes, while finalize
   includes full static validation. Network/Pi filesystem profiling is needed
   before changing this area. File-integrity and changed-policy checks must not
   be removed.

4. **Exact preview is smaller than generation on these cases:** 84 ms indexing
   plus 197 ms geometry for 133,225 moves. Generation already returns its exact
   `JobPlan`; the main-window path reuses it, and Preview/export/send do not
   regenerate image sampling just to reuse the prepared job. Reopening Preview
   can reconstruct Qt paths; it normally reuses the prepared plan/index. A
   binding that actually changes program bytes correctly requires a new plan.

5. **Header and asset work is repeated but small here.** Five header probes and
   four stable payload reads occur across this benchmark's canvas load, structured
   preflight, two generations and identity verification. Each individual header/
   identity stage is under 7 ms at the median. Identity rereads at Preview,
   export and send deliberately reject changed source bytes. Cleanup canvas/dialog
   paths use both color and grayscale decodes for their different representations;
   their cost for large photographs was not measured here. This is a profiling
   candidate, not a demonstrated bottleneck.

No input-to-export automatic retry or regeneration loop was found. Remote chunk
upload advances a bounded offset; failure exits. Lost-START recovery polls the
same job identity until its bounded deadline and does not retry START. Background
monitor polling is separate from generation. Source-change rejection requests
explicit regeneration. These are code-trace findings; failure-network timing
was not exercised in this offline benchmark.

## Overscan change: measured cost and movement reduction

The same three cases were repeated three times against the raster-overscan
working tree with an explicit **synthetic offline** verified envelope. The
record is benchmark data only and does not qualify any real machine or settings.
The original surface checks and the new serialized movement/surface checks all
ran. The comparison includes all changes in this feature; it does not isolate
individual optimizations from added safety-validation work.

| Current feature | White border | Near-white border | Gray dither |
| --- | ---: | ---: | ---: |
| Commands, baseline → current | 2,246 → 1,797 | 4,972 → 4,533 | 133,961 → 133,597 |
| Laser-off movement mm, baseline → current | 18,048.323 → 2,346.528 | 24,189.367 → 12,252.118 | 36,492.383 → 36,400.861 |
| Powered movement mm, unchanged | 24,822.500 | 25,482.796 | 32,987.240 |
| Cold generation ms | 90.417 | 163.234 | 3,840.390 |
| Explicit warm regeneration ms | 85.098 | 153.351 | 3,856.600 |
| Preview index ms | 0.875 | 2.241 | 73.363 |
| Offscreen preview geometry ms | 3.613 | 5.626 | 171.912 |
| One local preflight ms | 13.343 | 35.329 | 1,021.454 |
| Offline finalize ms | 48.030 | 73.534 | 1,089.934 |
| Committed-file recheck ms | 8.430 | 9.818 | 27.680 |

White-border laser-off movement falls 87.0%; near-white-border movement falls
49.3%, while the dithered border remains powered wherever sampling requires it.
Dense generation is 16.2% slower than baseline and its one-pass preflight is
73.4% slower. These are measured additional costs of the complete change, whose
new boundaries are checked for every relevant serialized segment; this feature
is not presented as a general CPU speedup. The duplicate-parser call counts
remain unchanged. The ranked follow-ups above therefore still apply; their
times describe the explicitly labeled baseline.

The current source also collects preview bounds in a streaming pass instead of
allocating two endpoint lists. The preview timings alone do not isolate that
change because the generated movement count also changed.

For all three cases, SHA-256 of the complete ordered powered-move tuples
`(start_x, start_y, end_x, end_y, power, feed_mm_min)` matches the baseline
exactly. This verifies the serialized placement, power, feed and scan ordering
of every powered segment in those representative jobs, beyond equal total cut
length. Evidence: `build/raster-pipeline-baseline-hashes.json` compared with
`build/raster-pipeline-current-final.json`.

These final feature medians include the exact powered beam/head authority
recheck, actual-approach support and strict raster-directive ordering changes.
They were rerun after the backend test process stopped, without the full test
suite competing for CPU. The final benchmark asserts that its application
source tree, tracked application diff and benchmark source remain unchanged
throughout measurement. The 185 application Python files include untracked new
modules; a plain Git revision alone would not identify this working-tree run.

- Base revision: `52f25b4f7dec67f8ecfb6b69d7b39e627edc8573`.
- Application source-tree SHA-256: `013d1e558ad740c993ad8b10c7df58786a035e62d078918dbd03d680142b0337`.
- Tracked application diff SHA-256: `11e5cf7a2d6bb70594ef6c06539a3ffaa424023170a4d8205648bf37e3fc73dc`.
- Benchmark script SHA-256: `f9449296a384b327db7099d8700622ce9b6c40c04bf1ecc508f69aab77d85d9a`.

## Regression for the duplication fixed earlier on October 15

The earlier `2a22fa5` change removed repeated generic/runtime parsing and
per-command configuration resolution. The comparison extracts only four static
validator methods from its local parent revision, calls them offline on the
same generated text, and checks identical canonical lines, digest and powered
classification. It does not execute an old application or old controller code.

| Same-job preflight (ms) | Before October 15 fix | Current baseline | Ratio |
| --- | ---: | ---: | ---: |
| White border | 71.160 | 10.983 | 6.48× |
| Near-white border | 167.638 | 23.217 | 7.22× |
| Gray dither | 4,835.978 | 589.126 | 8.21× |

For the same 134-command canonical sample, total `parse_words` calls fall from
406 to 9, `_check_line_safety` calls from 134 to zero, and air-assist configuration
resolution calls from 1,073 to 7. Of the current nine parse calls, five belong to
stream/modal validation and four to first-motion discovery. The strict canonical
motion parser still checks feed, power and syntax; the full allowlist handles
noncanonical syntax.

`tests/test_raster_pipeline_performance.py` adds seven passing regressions:
1/128/2,048 canonical and noncanonical moves retain constant configuration work,
one generic parse per noncanonical move, no duplicate runtime parse, and no
reparse of the same immutable proof. Changed motion authority and a powered
rapid tail remain rejected. These are offline static-validation tests.

## Reproduction and evidence

Use the checkout's supported Python environment to run:

```text
python scripts/benchmark_raster_pipeline.py --verified-envelope --repeats 3 --counts --output build/raster-pipeline-current-final.json
python scripts/benchmark_raster_pipeline.py --source-root ../background-cleanup-dev --repeats 3 --counts --legacy-reference 2a22fa5^ --output build/raster-pipeline-baseline.json
python -m pytest -q tests/test_raster_pipeline_performance.py
```

The optional source root must identify the intended local checkout. The script
records its revision and dirty status. Generated sources, G-code and store data
are temporary under ignored `build/`; raw result files remain ignored and are
not curated project fixtures. Baseline evidence is
`build/raster-pipeline-baseline.json`; the separate validator-call sample is
`build/raster-validator-counts.json` in the raster-overscan feature checkout.
Final feature evidence is `build/raster-pipeline-current-final.json`; the earlier
`build/raster-pipeline-current.json` is superseded by this final measurement.
Ruff and compilation passed for the new benchmark and regression file.
