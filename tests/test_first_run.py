from __future__ import annotations

from pathlib import Path

from laser_aligner.first_run import build_hardware_config, save_hardware_setup
from laser_aligner.storage import read_json


def test_build_hardware_config_uses_network_bridges() -> None:
    payload = build_hardware_config(
        Path("config/default.json"),
        host="10.0.0.42",
        controller_port=8765,
        camera_port=8766,
        width_mm=300.0,
        height_mm=200.0,
        camera_width=1920,
        camera_height=1080,
        autofocus=False,
        focus_value=25,
    )
    assert payload["app"]["simulation"] is False
    assert payload["app"]["data_dir"] == "../data"
    assert payload["camera"]["device"] == "e3camera://10.0.0.42:8766"
    assert payload["camera"]["controls"]["focus_auto"] == 0
    assert payload["camera"]["controls"]["focus_absolute"] == 25
    assert payload["machine"]["backend"] == "serial"
    assert payload["machine"]["port"] == "e3bridge://10.0.0.42:8765"
    assert payload["machine"]["work_area"]["x_max"] == 300.0
    assert payload["machine"]["work_area"]["y_max"] == 200.0
    assert payload["machine"]["photo_position"]["x"] == 150.0
    assert payload["machine"]["photo_position"]["y"] == 100.0
    assert payload["machine"]["allow_motion"] is True


def test_save_hardware_setup_is_user_state(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("E3_USER_STATE_DIR", str(tmp_path))
    config_path = save_hardware_setup(
        Path("config/default.json"),
        bridge_token="x" * 32,
        host="10.0.0.42",
        controller_port=8765,
        camera_port=8766,
        width_mm=220.0,
        height_mm=220.0,
    )
    assert config_path == tmp_path / "config" / "network-local.json"
    assert config_path.is_file()
    assert (tmp_path / "secrets" / "bridge-token.txt").read_text() == "x" * 32
    state = read_json(tmp_path / "first-run.json")
    assert state["configured"] is True
    assert state["deferred"] is False
