from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.skipif(
    importlib.util.find_spec("PySide6") is None,
    reason="PySide6 is required for desktop theme tests",
)
def test_theme_rendering_and_interaction_in_fresh_application(tmp_path):
    # Installing an application-wide theme repolishes every existing widget.
    # Keep these assertions independent of other modules' native Qt lifetimes.
    root = Path(__file__).resolve().parents[1]
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith("PYTEST_")}
    environment["QT_QPA_PLATFORM"] = "offscreen"
    environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    command = [sys.executable, "-m", "pytest", "-q", "-o", "addopts=",
               str(root / "tests/ui_theme_probe.py"),
               "--basetemp", str(tmp_path / "theme-probe")]
    try:
        result = subprocess.run(
            command, cwd=root, env=environment, capture_output=True, text=True,
            timeout=90, check=False,
        )
    except subprocess.TimeoutExpired as error:
        pytest.fail(
            f"Isolated Qt theme probe exceeded 90 seconds.\n"
            f"stdout:\n{error.stdout or ''}\nstderr:\n{error.stderr or ''}"
        )
    assert result.returncode == 0, (
        f"Isolated Qt theme probe failed ({result.returncode}).\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
