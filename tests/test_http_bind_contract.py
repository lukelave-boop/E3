from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from laser_aligner import __main__ as browser_main
from laser_aligner.config import ConfigError, load_settings


def _write_config(
    tmp_path: Path,
    *,
    host: object = "127.0.0.1",
    port: object = 8080,
) -> Path:
    path = tmp_path / "http.json"
    path.write_text(
        json.dumps(
            {
                "app": {
                    "data_dir": str(tmp_path / "runtime"),
                    "host": host,
                    "open_browser": False,
                    "port": port,
                }
            }
        ),
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize(
    ("configured", "normalized"),
    [
        ("127.0.0.1", "127.0.0.1"),
        (" LOCALHOST ", "localhost"),
        ("0.0.0.0", "0.0.0.0"),
    ],
)
def test_config_accepts_only_supported_ipv4_bind_spellings(
    tmp_path: Path,
    configured: str,
    normalized: str,
) -> None:
    settings = load_settings(_write_config(tmp_path, host=configured))

    assert settings.app.host == normalized


@pytest.mark.parametrize(
    "configured",
    ["::1", "::", "[::1]", "192.168.1.50", "127.0.0.2", "", 127001],
)
def test_config_rejects_unsupported_or_non_string_bind_hosts(
    tmp_path: Path,
    configured: object,
) -> None:
    with pytest.raises(ConfigError, match="IPv4 loopback or wildcard bind"):
        load_settings(_write_config(tmp_path, host=configured))


@pytest.mark.parametrize("configured", [1, 8080, 65535])
def test_config_accepts_valid_integer_ports(
    tmp_path: Path,
    configured: int,
) -> None:
    settings = load_settings(_write_config(tmp_path, port=configured))

    assert settings.app.port == configured


@pytest.mark.parametrize("configured", [0, -1, 65536, 8080.5, "8080", True, None])
def test_config_rejects_out_of_range_or_non_integer_ports(
    tmp_path: Path,
    configured: object,
) -> None:
    with pytest.raises(ConfigError, match="integer between 1 and 65535"):
        load_settings(_write_config(tmp_path, port=configured))


def test_cli_host_override_uses_the_same_normalized_bind_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, Any] = {}

    class FakeContext:
        def __init__(self, settings: Any, *, hardware_enabled: bool) -> None:
            observed["settings"] = settings
            observed["hardware_enabled"] = hardware_enabled

        def start(self) -> None:
            observed["context_started"] = True

        def stop(self) -> None:
            observed["context_stopped"] = True

    class FakeServer:
        def __init__(self, address: tuple[str, int], context: FakeContext) -> None:
            observed["address"] = address
            observed["context"] = context

        def serve_forever(self, *, poll_interval: float) -> None:
            observed["poll_interval"] = poll_interval

        def server_close(self) -> None:
            observed["server_closed"] = True

    monkeypatch.setattr(browser_main, "AppContext", FakeContext)
    monkeypatch.setattr(browser_main, "AppHTTPServer", FakeServer)
    monkeypatch.setattr(browser_main, "configure_logging", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(browser_main.signal, "signal", lambda *_args: None)

    result = browser_main.main(
        [
            "--config",
            str(_write_config(tmp_path)),
            "--host",
            " LOCALHOST ",
            "--port",
            "65535",
        ]
    )

    assert result == 0
    assert observed["address"] == ("localhost", 65535)
    assert observed["context_started"] is True
    assert observed["context_stopped"] is True
    assert observed["server_closed"] is True


@pytest.mark.parametrize("override", ["::1", "::", "192.168.1.50", ""])
def test_cli_host_override_rejects_unsupported_binds_before_startup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    override: str,
) -> None:
    def fail_context(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("invalid bind reached application startup")

    monkeypatch.setattr(browser_main, "AppContext", fail_context)

    result = browser_main.main(
        ["--config", str(_write_config(tmp_path)), "--host", override]
    )

    assert result == 2
    assert "IPv4 loopback or wildcard bind" in capsys.readouterr().err


@pytest.mark.parametrize("override", ["0", "-1", "65536"])
def test_cli_port_override_rejects_invalid_range_before_startup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    override: str,
) -> None:
    def fail_context(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("invalid port reached application startup")

    monkeypatch.setattr(browser_main, "AppContext", fail_context)

    result = browser_main.main(
        ["--config", str(_write_config(tmp_path)), "--port", override]
    )

    assert result == 2
    assert "integer between 1 and 65535" in capsys.readouterr().err
