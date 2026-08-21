import json
from pathlib import Path
from typing import Any

import pytest

from laser_aligner import __main__ as browser_main


def _config(tmp_path: Path) -> Path:
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "app": {
                    "data_dir": str(tmp_path / "runtime"),
                    "open_browser": False,
                }
            }
        ),
        encoding="utf-8",
    )
    return path


def _patch_runtime(
    monkeypatch: pytest.MonkeyPatch,
    context_type: type,
    server_type: type,
) -> None:
    class FakeRuntime:
        def __init__(
            self,
            settings: Any,
            *,
            hardware_enabled: bool,
            laser_lockout: bool,
        ) -> None:
            self.settings = settings
            self.context = context_type(
                settings,
                hardware_enabled=hardware_enabled,
                laser_lockout=laser_lockout,
            )

        def start(self) -> None:
            self.context.start()

        def stop(self) -> None:
            self.context.stop()

    monkeypatch.setattr(browser_main, "CoreRuntime", FakeRuntime)
    monkeypatch.setattr(browser_main, "AppHTTPServer", server_type)
    monkeypatch.setattr(browser_main, "configure_logging", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(browser_main.signal, "signal", lambda *_args: None)


def test_server_bind_failure_stops_started_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class FakeContext:
        def __init__(
            self,
            settings: Any,
            *,
            hardware_enabled: bool,
            laser_lockout: bool,
        ) -> None:
            events.append("constructed")

        def start(self) -> None:
            events.append("started")

        def stop(self) -> None:
            events.append("stopped")

    class FailingServer:
        def __init__(self, address: tuple[str, int], context: FakeContext) -> None:
            raise OSError("address already in use")

    _patch_runtime(monkeypatch, FakeContext, FailingServer)

    with pytest.raises(OSError, match="address already in use"):
        browser_main.main(["--config", str(_config(tmp_path))])

    assert events == ["constructed", "started", "stopped"]


def test_partial_context_start_failure_still_requests_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class FailingContext:
        def __init__(
            self,
            settings: Any,
            *,
            hardware_enabled: bool,
            laser_lockout: bool,
        ) -> None:
            events.append("constructed")

        def start(self) -> None:
            events.append("starting")
            raise RuntimeError("partial startup")

        def stop(self) -> None:
            events.append("stopped")

    class UnusedServer:
        def __init__(self, address: tuple[str, int], context: FailingContext) -> None:
            raise AssertionError("server construction must not be reached")

    _patch_runtime(monkeypatch, FailingContext, UnusedServer)

    with pytest.raises(RuntimeError, match="partial startup"):
        browser_main.main(["--config", str(_config(tmp_path))])

    assert events == ["constructed", "starting", "stopped"]


def test_server_close_failure_cannot_skip_context_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class FakeContext:
        def __init__(
            self,
            settings: Any,
            *,
            hardware_enabled: bool,
            laser_lockout: bool,
        ) -> None:
            events.append("constructed")

        def start(self) -> None:
            events.append("started")

        def stop(self) -> None:
            events.append("stopped")

    class FailingCloseServer:
        def __init__(self, address: tuple[str, int], context: FakeContext) -> None:
            events.append("server constructed")

        def serve_forever(self, *, poll_interval: float) -> None:
            events.append("served")

        def server_close(self) -> None:
            events.append("closing")
            raise OSError("close failed")

        def shutdown(self) -> None:
            events.append("shutdown")

    _patch_runtime(monkeypatch, FakeContext, FailingCloseServer)

    with pytest.raises(OSError, match="close failed"):
        browser_main.main(["--config", str(_config(tmp_path))])

    assert events == [
        "constructed",
        "started",
        "server constructed",
        "served",
        "closing",
        "stopped",
    ]
