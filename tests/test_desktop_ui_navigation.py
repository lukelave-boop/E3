from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.skipif(
    importlib.util.find_spec("PySide6") is None,
    reason="PySide6 is required for desktop navigation tests",
)
def test_navigation_rendering_and_interaction_in_fresh_application(tmp_path):
    # Font changes repolish all existing widgets, including unrelated tests' ones.
    # Keep the complete navigation probe in a process that owns its QApplication.
    root = Path(__file__).resolve().parents[1]
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith("PYTEST_")}
    environment["QT_QPA_PLATFORM"] = "offscreen"
    environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    command = [sys.executable, "-m", "pytest", "-q", "-o", "addopts=",
               str(root / "tests/ui_navigation_probe.py"),
               "--basetemp", str(tmp_path / "navigation-probe")]
    try:
        result = subprocess.run(
            command, cwd=root, env=environment, capture_output=True, text=True,
            timeout=90, check=False,
        )
    except subprocess.TimeoutExpired as error:
        pytest.fail(
            f"Isolated Qt navigation probe exceeded 90 seconds.\n"
            f"stdout:\n{error.stdout or ''}\nstderr:\n{error.stderr or ''}"
        )
    assert result.returncode == 0, (
        f"Isolated Qt navigation probe failed ({result.returncode}).\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
