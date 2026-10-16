"""Explicit pre-streaming payloads for historical installer-engine tests.

Current service sources require a coupled receiver/codec update. Old installers
must reject those sources; their own backup/hash/atomicity tests still exercise
the pre-streaming payload contract. Exact import checks use the recorded Git
service when available. Shallow CI uses a clearly marked representative source
for installer-only checks and never claims an exact historical import result.
"""
from __future__ import annotations

import subprocess
from contextlib import contextmanager
from pathlib import Path

import pytest

REVISION = "aac45cdb58f1e746ecfd4c08624fd74230c172f4"
SERVICE = "laser_aligner/machine/service.py"
ROOT = Path(__file__).resolve().parents[1]
REPRESENTATIVE = b"""# Representative pre-streaming source for installer tests only.
from ..gcode.limits import MAX_STREAM_COMMANDS
from ..operation_focus import PREFIX
from ..raster_safety import RasterTravelEnvelope
from .material_surface import CAPABILITY
"""


def source_tree(destination):
    destination.mkdir(parents=True, exist_ok=True)
    names = [path.relative_to(ROOT) for path in (ROOT / "laser_aligner").rglob("*.py")
             if path.name != "grbl_stream.py"]
    names += [path.relative_to(ROOT) for path in (ROOT / "scripts").glob("install_*.py")]
    names += [path.relative_to(ROOT) for path in (ROOT / "docs").glob("*.md")]
    for name in names:
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((ROOT / name).read_bytes())
    try:
        previous = subprocess.run(
            ["git", "show", f"{REVISION}:{SERVICE}"], cwd=ROOT, capture_output=True, check=False,
        )
    except OSError:
        previous = None
    if previous is not None and previous.returncode == 0:
        (destination / SERVICE).write_bytes(previous.stdout)
    else:
        (destination / SERVICE).write_bytes(REPRESENTATIVE)
        (destination / ".representative-service").write_text("Installer-only fixture; no exact import claim.\n")
    return destination


def require_exact(tree):
    if (tree / ".representative-service").exists():
        pytest.skip("The exact pre-streaming service source is unavailable in this shallow checkout")


@contextmanager
def selected_source(builder, destination):
    tree = source_tree(destination)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(builder, "ROOT", tree)
        yield tree
