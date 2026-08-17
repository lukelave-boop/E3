import json
from pathlib import Path

import pytest

import laser_aligner.storage as storage


def test_read_json_returns_default_for_non_utf8_data(tmp_path: Path) -> None:
    path = tmp_path / "invalid.json"
    path.write_bytes(b"\xff\xfe")
    fallback = {"usable": False}

    assert storage.read_json(path, fallback) is fallback


@pytest.mark.parametrize(
    "text",
    [
        '{"state": 1, "state": 2}',
        '{"nested": {"value": 1, "value": 2}}',
        '{"measurement": NaN}',
        '{"measurement": Infinity}',
        '{"measurement": -Infinity}',
    ],
)
def test_read_json_returns_default_for_ambiguous_or_nonstandard_json(
    tmp_path: Path,
    text: str,
) -> None:
    path = tmp_path / "invalid.json"
    path.write_text(text, encoding="utf-8")
    fallback = {"usable": False}

    assert storage.read_json(path, fallback) is fallback


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_atomic_json_rejects_nonstandard_numbers_without_replacing_destination(
    tmp_path: Path,
    value: float,
) -> None:
    path = tmp_path / "state.json"
    path.write_text('{"old": true}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="Out of range float values"):
        storage.atomic_write_json(path, {"measurement": value})

    assert json.loads(path.read_text(encoding="utf-8")) == {"old": True}
    assert list(tmp_path.glob(".state.json.*")) == []


def test_atomic_publishers_sync_the_parent_after_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    synced: list[Path] = []
    monkeypatch.setattr(storage, "_fsync_parent_directory", synced.append)

    json_path = tmp_path / "state.json"
    bytes_path = tmp_path / "frame.bin"
    unique_path = tmp_path / "capture.bin"
    storage.atomic_write_json(json_path, {"ok": True})
    storage.atomic_write_bytes(bytes_path, b"frame")
    assert storage.atomic_write_bytes_if_absent(unique_path, b"capture")
    assert not storage.atomic_write_bytes_if_absent(unique_path, b"replacement")

    assert synced == [json_path, bytes_path, unique_path]
    assert unique_path.read_bytes() == b"capture"

def test_windows_publish_if_absent_uses_atomic_no_replace_rename(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    temporary = tmp_path / "temporary.bin"
    destination = tmp_path / "destination.bin"
    temporary.write_bytes(b"complete")
    renamed: list[tuple[Path, Path]] = []

    def rename(source: Path, target: Path) -> None:
        renamed.append((source, target))
        storage.os.replace(source, target)

    monkeypatch.setattr(storage.os, "name", "nt")
    monkeypatch.setattr(storage.os, "rename", rename)

    assert storage._publish_temp_if_absent(temporary, destination)
    assert renamed == [(temporary, destination)]
    assert destination.read_bytes() == b"complete"
    assert not temporary.exists()


def test_windows_publish_if_absent_preserves_existing_destination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    temporary = tmp_path / "temporary.bin"
    destination = tmp_path / "destination.bin"
    temporary.write_bytes(b"new")
    destination.write_bytes(b"existing")

    def already_exists(_source: Path, _target: Path) -> None:
        raise FileExistsError("destination exists")

    monkeypatch.setattr(storage.os, "name", "nt")
    monkeypatch.setattr(storage.os, "rename", already_exists)

    assert not storage._publish_temp_if_absent(temporary, destination)
    assert destination.read_bytes() == b"existing"
    assert temporary.read_bytes() == b"new"


def test_windows_publish_if_absent_accepts_native_already_exists_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    temporary = tmp_path / "temporary.bin"
    destination = tmp_path / "destination.bin"
    temporary.write_bytes(b"new")
    destination.write_bytes(b"existing")

    def already_exists(_source: Path, _target: Path) -> None:
        error = OSError("destination exists")
        error.winerror = 183
        raise error

    monkeypatch.setattr(storage.os, "name", "nt")
    monkeypatch.setattr(storage.os, "rename", already_exists)

    assert not storage._publish_temp_if_absent(temporary, destination)
    assert destination.read_bytes() == b"existing"

