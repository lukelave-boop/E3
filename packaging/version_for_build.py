from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


def _shared_versioning() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "laser_aligner" / "versioning.py"
    spec = importlib.util.spec_from_file_location("e3_shared_versioning", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load shared E3 versioning from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_shared = _shared_versioning()

version_from_commit_count = _shared.version_from_commit_count
build_version = _shared.build_version
application_version = _shared.application_version
BASELINE_REVISION = _shared.BASELINE_REVISION


if __name__ == "__main__":
    print(build_version())
