# E3 planning golden baseline

These curated fixtures freeze observable planning behavior before the staged
planning-pipeline refactor.

Baseline commit:

`d083815c5f4645af5ddcc30e4d1187e4742db718`

The first case is intentionally small: one fixed-ID, sharp-corner 40 x 25 mm
rectangle in a fixed 100 x 100 mm machine-coordinate project. It runs through
the production `generate_project_gcode()` entry point with no Qt, camera,
controller, Pi, serial connection, or hardware access.

Successful cases record three artifacts:

- `program.gcode` - the generated controller program. Only the human-readable
  `; Generated:` timestamp is canonicalized to `<TIMESTAMP>`; command ordering,
  coordinates, feed, power, metadata, and laser-off commands remain exact.
- `result.json` - the complete stable `ProjectJob` summary outside the raw
  program and `JobPlan`.
- `preview.json` - the immutable `JobPlan` built from the exact generated
  program, including moves, bounds, laser state, power, feeds, distances,
  timing, warnings, and planner metadata.

Finite floating-point values in the semantic JSON artifacts are canonicalized
to 12 decimal places. This removes meaningless last-bit differences across
Python, NumPy, and operating-system combinations while preserving changes as
small as one trillionth of a millimetre or second. `program.gcode` remains an
exact text comparison after timestamp and newline normalization.

Expected-rejection cases instead record a single `rejection.json` containing
the exception type and stable rejection message.

Golden files are read-only during normal tests. Missing or changed fixtures
fail the test. Regeneration is deliberately separate and requires `--accept`.

Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe scripts\update_planning_goldens.py --case simple_rectangle --accept
.\.venv\Scripts\python.exe -m pytest -q tests\test_planning_goldens.py
.\.venv\Scripts\python.exe -m ruff check tests\planning_golden_support.py tests\test_planning_goldens.py scripts\update_planning_goldens.py
git diff -- tests/golden/planning
```

Linux:

```bash
.venv/bin/python scripts/update_planning_goldens.py --case simple_rectangle --accept
.venv/bin/python -m pytest -q tests/test_planning_goldens.py
.venv/bin/python -m ruff check tests/planning_golden_support.py tests/test_planning_goldens.py scripts/update_planning_goldens.py
git diff -- tests/golden/planning
```

Do not regenerate a golden merely because a test fails. First determine whether
the planning change is intended. If it is intended, regenerate explicitly and
review the exact program, result, and preview diff before accepting it.

