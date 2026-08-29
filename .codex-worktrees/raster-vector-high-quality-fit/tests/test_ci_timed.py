from __future__ import annotations

import sys
from pathlib import Path

import pytest

from tools.ci_timed import main


def test_ci_timed_runs_command_and_writes_duration_summary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))

    result = main(["test phase", "--", sys.executable, "-c", "raise SystemExit(0)"])

    assert result == 0
    assert summary.read_text(encoding="utf-8").startswith("- test phase: ")
    assert summary.read_text(encoding="utf-8").endswith(" seconds\n")


def test_ci_timed_preserves_command_failure_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)

    result = main(["failed phase", "--", sys.executable, "-c", "raise SystemExit(7)"])

    assert result == 7
