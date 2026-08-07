from __future__ import annotations

from types import SimpleNamespace

import pytest

from laser_aligner.camera import controls


def test_apply_controls_reports_verified_values_and_readback_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(controls.shutil, "which", lambda _name: "/usr/bin/v4l2-ctl")

    def fake_run(command: list[str], **_kwargs: object) -> SimpleNamespace:
        if "--list-ctrls-menus" in command:
            return SimpleNamespace(
                returncode=0,
                stdout=(
                    "focus_absolute 0x009a090a (int) : min=0 max=250 value=40\n"
                    "gain 0x00980913 (int) : min=0 max=255 value=0\n"
                ),
                stderr="",
            )
        if any(value.startswith("--get-ctrl=") for value in command):
            return SimpleNamespace(
                returncode=0,
                stdout="focus_absolute: 40\ngain: 3\n",
                stderr="",
            )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(controls.subprocess, "run", fake_run)
    result = controls.apply_controls(
        "/dev/video0",
        {"focus_absolute": 40, "gain": 0, "focus_auto": 0},
    )

    assert result.verified == {"focus_absolute": 40, "gain": 3}
    assert result.applied == {"focus_absolute": 40}
    assert "readback returned 3" in result.skipped["gain"]
    assert "not exposed" in result.skipped["focus_auto"]
