from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from laser_aligner.camera import controls


def test_missing_v4l2_tool_marks_configured_quality_controls_unverified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(controls.shutil, "which", lambda _name: None)

    result = controls.apply_controls(
        "/dev/video0",
        {
            "focus_auto": 0,
            "focus_absolute": 40,
            "exposure_auto": 1,
            "exposure_time_absolute": 250,
        },
    )

    assert result.applied == {}
    assert result.critical_unverified == {
        "exposure_mode": "v4l2-ctl is not installed",
        "exposure_value": "v4l2-ctl is not installed",
        "focus_mode": "v4l2-ctl is not installed",
        "focus_value": "v4l2-ctl is not installed",
    }


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


def test_control_aliases_resolve_and_modes_apply_before_absolute_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(controls.shutil, "which", lambda _name: "/usr/bin/v4l2-ctl")
    commands: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> SimpleNamespace:
        commands.append(command)
        if "--list-ctrls-menus" in command:
            return SimpleNamespace(
                returncode=0,
                stdout=(
                    "auto_exposure 0x009a0901 (menu) : min=0 max=3 step=1 value=3\n"
                    "exposure_time_absolute 0x009a0902 (int) : min=3 max=2047 step=1 value=250\n"
                ),
                stderr="",
            )
        if any(value.startswith("--get-ctrl=") for value in command):
            return SimpleNamespace(
                returncode=0,
                stdout=(
                    "auto_exposure: 1 (Manual Mode)\n"
                    "exposure_time_absolute: 250\n"
                ),
                stderr="",
            )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(controls.subprocess, "run", fake_run)
    result = controls.apply_controls(
        "/dev/video0",
        {
            "exposure_auto": 1,
            "auto_exposure": 1,
            "exposure_time_absolute": 250,
        },
    )

    setters = [
        next(value for value in command if value.startswith("--set-ctrl="))
        for command in commands
        if any(value.startswith("--set-ctrl=") for value in command)
    ]
    assert setters == [
        "--set-ctrl=auto_exposure=1",
        "--set-ctrl=exposure_time_absolute=250",
    ]
    assert result.satisfied == {
        "exposure_mode": "auto_exposure",
        "exposure_value": "exposure_time_absolute",
    }
    assert result.critical_unverified == {}
    assert result.skipped["exposure_auto"] == "alias resolved through auto_exposure"


def test_readback_rejects_unrecognized_text_after_numeric_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(controls.shutil, "which", lambda _name: "/usr/bin/v4l2-ctl")
    monkeypatch.setattr(
        controls.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout="auto_exposure: 1 unexpected\n",
            stderr="",
        ),
    )

    assert controls.read_control_values(
        "/dev/video0",
        {"auto_exposure": 1},
    ) == {}


def test_control_value_outside_device_range_is_not_written(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(controls.shutil, "which", lambda _name: "/usr/bin/v4l2-ctl")
    commands: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> SimpleNamespace:
        commands.append(command)
        if "--list-ctrls-menus" in command:
            return SimpleNamespace(
                returncode=0,
                stdout=(
                    "focus_absolute 0x009a090a (int) : "
                    "min=0 max=250 step=5 value=40\n"
                ),
                stderr="",
            )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(controls.subprocess, "run", fake_run)
    result = controls.apply_controls("/dev/video0", {"focus_absolute": 252})

    assert not any(
        value.startswith("--set-ctrl=") for command in commands for value in command
    )
    assert "outside the exposed range" in result.skipped["focus_absolute"]
    assert "focus_value" in result.critical_unverified


def test_control_value_between_limits_but_off_device_step_is_not_written(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(controls.shutil, "which", lambda _name: "/usr/bin/v4l2-ctl")
    commands: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> SimpleNamespace:
        commands.append(command)
        if "--list-ctrls-menus" in command:
            return SimpleNamespace(
                returncode=0,
                stdout=(
                    "focus_absolute 0x009a090a (int) : "
                    "min=0 max=250 step=5 value=5\n"
                ),
                stderr="",
            )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(controls.subprocess, "run", fake_run)
    result = controls.apply_controls("/dev/video0", {"focus_absolute": 4})

    assert not any(
        value.startswith("--set-ctrl=") for command in commands for value in command
    )
    assert "outside the exposed range/step" in result.skipped["focus_absolute"]
    assert "focus_value" in result.critical_unverified


def test_control_probe_timeout_is_bounded_and_reported_as_unverified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(controls.shutil, "which", lambda _name: "/usr/bin/v4l2-ctl")

    def timeout(command: list[str], **kwargs: object) -> SimpleNamespace:
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(controls.subprocess, "run", timeout)
    result = controls.apply_controls(
        "/dev/video0",
        {"focus_auto": 0, "focus_absolute": 40},
        timeout_seconds=0.02,
    )

    assert result.applied == {}
    assert all("timed out" in reason for reason in result.skipped.values())
    assert set(result.critical_unverified) == {"focus_mode", "focus_value"}


def test_cancelled_control_operation_does_not_spawn_v4l2(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(controls.shutil, "which", lambda _name: "/usr/bin/v4l2-ctl")
    monkeypatch.setattr(
        controls.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("Cancelled controls must not spawn v4l2-ctl"),
    )

    result = controls.apply_controls(
        "/dev/video0",
        {"exposure_time_absolute": 250},
        cancelled=lambda: True,
    )

    assert "cancelled" in result.skipped["exposure_time_absolute"]
    assert "exposure_value" in result.critical_unverified
