from __future__ import annotations

import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from laser_aligner import updates
from laser_aligner.updates import UpdateError


def test_public_windows_launcher_passes_exact_installer_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = tmp_path / "E3-Setup.exe"
    package.write_bytes(b"verified installer")
    monkeypatch.setattr(updates.sys, "platform", "win32")
    monkeypatch.delattr(updates.sys, "_MEIPASS", raising=False)
    captured: dict[str, object] = {}

    def start(
        argv: Sequence[str],
        *,
        cwd: Path,
        environment: Mapping[str, str],
        description: str,
    ) -> object:
        captured.update(
            argv=list(argv),
            cwd=cwd,
            environment=environment,
            description=description,
        )
        return object()

    monkeypatch.setattr(updates, "_start_external_windows_process", start)

    updates.launch_downloaded_update(package)

    assert captured["argv"] == [
        str(package.resolve()),
        "/SP-",
        "/CLOSEAPPLICATIONS",
        "/RESTARTAPPLICATIONS",
        "/NORESTART",
    ]
    assert captured["cwd"] == package.parent.resolve()
    assert captured["description"] == "Windows update installer"
    assert captured["environment"] == dict(os.environ)
    assert captured["environment"] is not os.environ


def test_public_launcher_rejects_missing_package(tmp_path: Path) -> None:
    missing = tmp_path / "E3-Setup.exe"

    with pytest.raises(UpdateError, match="Downloaded update package does not exist"):
        updates.launch_downloaded_update(missing)


def test_windows_installer_rejects_non_executable_package(tmp_path: Path) -> None:
    package = tmp_path / "E3-Setup.zip"
    package.write_bytes(b"not an installer")

    with pytest.raises(UpdateError, match=r"must be an \.exe installer"):
        updates._launch_windows_installer(package)


def test_installer_environment_removes_only_bundle_rooted_path_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle_root = r"C:\E3\_internal"
    original_path_entries = [
        f'"{bundle_root}"',
        r"c:\e3\_INTERNAL\runtime",
        r"%E3_TEST_BUNDLE%\plugins",
        r"C:\E3\_internal-tools",
        r"C:\Windows\System32",
        r"D:\User Tools",
        "relative-tools",
    ]
    original_path = ";".join(original_path_entries)
    monkeypatch.setattr(updates.sys, "_MEIPASS", bundle_root, raising=False)
    monkeypatch.setenv("E3_TEST_BUNDLE", bundle_root)
    monkeypatch.setenv("E3_USER_STATE", "keep-this-value")
    monkeypatch.setenv("PATH", original_path)

    child_environment = updates._windows_installer_environment()

    assert child_environment["PATH"] == ";".join(
        [
            r"C:\E3\_internal-tools",
            r"C:\Windows\System32",
            r"D:\User Tools",
            "relative-tools",
        ]
    )
    assert child_environment["E3_TEST_BUNDLE"] == bundle_root
    assert child_environment["E3_USER_STATE"] == "keep-this-value"
    assert os.environ["PATH"] == original_path
    assert child_environment is not os.environ


def test_external_windows_process_clears_dll_search_before_exact_popen(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    previous_directory = r"C:\Program Files\E3\_internal"
    events: list[tuple[str, object]] = []
    process = object()
    environment = {"PATH": r"C:\Windows\System32", "E3_USER_STATE": "keep"}

    def get_directory() -> str:
        events.append(("capture", previous_directory))
        return previous_directory

    def set_directory(value: str | None, *, operation: str) -> None:
        events.append((operation, value))

    def popen(argv: list[str], **kwargs: object) -> object:
        events.append(("popen", list(argv)))
        assert kwargs == {
            "cwd": str(tmp_path),
            "env": environment,
            "close_fds": True,
            "shell": False,
            "creationflags": (
                updates._WINDOWS_DETACHED_PROCESS
                | updates._WINDOWS_CREATE_NEW_PROCESS_GROUP
            ),
        }
        assert kwargs["env"] is not environment
        return process

    monkeypatch.setattr(updates, "_get_windows_dll_directory", get_directory)
    monkeypatch.setattr(updates, "_set_windows_dll_directory", set_directory)
    monkeypatch.setattr(updates.subprocess, "Popen", popen)

    result = updates._start_external_windows_process(
        [r"C:\Downloads\E3-Setup.exe", "/SP-"],
        cwd=tmp_path,
        environment=environment,
        description="Windows update installer",
    )

    assert result is process
    assert events == [
        ("capture", previous_directory),
        ("clear for external installer launch", None),
        ("popen", [r"C:\Downloads\E3-Setup.exe", "/SP-"]),
        ("restore after external installer launch", previous_directory),
    ]


def test_popen_failure_restores_dll_search_and_becomes_update_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    previous_directory = r"C:\Program Files\E3\_internal"
    events: list[tuple[str, object]] = []

    monkeypatch.setattr(
        updates,
        "_get_windows_dll_directory",
        lambda: previous_directory,
    )

    def set_directory(value: str | None, *, operation: str) -> None:
        events.append((operation, value))

    def popen(_argv: list[str], **_kwargs: object) -> object:
        events.append(("popen", None))
        raise OSError("simulated CreateProcess failure")

    monkeypatch.setattr(updates, "_set_windows_dll_directory", set_directory)
    monkeypatch.setattr(updates.subprocess, "Popen", popen)

    with pytest.raises(
        UpdateError,
        match=(
            "Could not create the Windows update installer process: "
            "simulated CreateProcess failure"
        ),
    ) as raised:
        updates._start_external_windows_process(
            [r"C:\Downloads\E3-Setup.exe"],
            cwd=tmp_path,
            environment={},
            description="Windows update installer",
        )

    assert isinstance(raised.value.__cause__, OSError)
    assert events == [
        ("clear for external installer launch", None),
        ("popen", None),
        (
            "restore after failed external installer launch",
            previous_directory,
        ),
    ]


def test_dll_search_clear_failure_is_distinct_and_never_starts_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    popen_called = False
    monkeypatch.setattr(
        updates,
        "_get_windows_dll_directory",
        lambda: r"C:\Program Files\E3\_internal",
    )

    def set_directory(_value: str | None, *, operation: str) -> None:
        raise UpdateError(f"Could not {operation} the Win32 DLL search directory")

    def popen(_argv: list[str], **_kwargs: object) -> object:
        nonlocal popen_called
        popen_called = True
        return object()

    monkeypatch.setattr(updates, "_set_windows_dll_directory", set_directory)
    monkeypatch.setattr(updates.subprocess, "Popen", popen)

    with pytest.raises(
        UpdateError,
        match="Could not clear for external installer launch the Win32 DLL search",
    ):
        updates._start_external_windows_process(
            [r"C:\Downloads\E3-Setup.exe"],
            cwd=tmp_path,
            environment={},
            description="Windows update installer",
        )

    assert popen_called is False


def test_popen_and_dll_restore_failures_are_both_reported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    previous_directory = r"C:\Program Files\E3\_internal"
    monkeypatch.setattr(
        updates,
        "_get_windows_dll_directory",
        lambda: previous_directory,
    )

    def set_directory(value: str | None, *, operation: str) -> None:
        if value == previous_directory:
            raise UpdateError(f"Could not {operation} the Win32 DLL search directory")

    def popen(_argv: list[str], **_kwargs: object) -> object:
        raise OSError("simulated CreateProcess failure")

    monkeypatch.setattr(updates, "_set_windows_dll_directory", set_directory)
    monkeypatch.setattr(updates.subprocess, "Popen", popen)

    with pytest.raises(UpdateError) as raised:
        updates._start_external_windows_process(
            [r"C:\Downloads\E3-Setup.exe"],
            cwd=tmp_path,
            environment={},
            description="Windows update installer",
        )

    message = str(raised.value)
    assert "simulated CreateProcess failure" in message
    assert "additionally" in message
    assert "restore after failed external installer launch" in message
    assert isinstance(raised.value.__cause__, OSError)


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="The external-launch smoke test exercises Windows CreateProcess",
)
def test_external_windows_process_smoke(tmp_path: Path) -> None:
    marker = tmp_path / "external-process-started.txt"
    child_code = (
        "from pathlib import Path; import sys; "
        "Path(sys.argv[1]).write_text('started', encoding='utf-8')"
    )

    process = updates._start_external_windows_process(
        [sys.executable, "-c", child_code, str(marker)],
        cwd=tmp_path,
        environment=updates._windows_installer_environment(),
        description="harmless Windows updater smoke",
    )
    try:
        assert process.wait(timeout=10) == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)

    assert marker.read_text(encoding="utf-8") == "started"
