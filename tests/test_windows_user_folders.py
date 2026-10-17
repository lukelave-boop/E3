from __future__ import annotations

import sys
from pathlib import Path

import pytest

from laser_aligner import windows_user_folders

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows known folders and junctions")


def test_two_build_homes_share_real_documents_without_sharing_appdata(tmp_path, monkeypatch):
    real = tmp_path / "OneDrive - Example & Co" / "Desktop"
    real.mkdir(parents=True)
    (real / "drawing.png").write_bytes(b"operator file")
    monkeypatch.setattr(windows_user_folders, "known_user_folders", lambda: {"Desktop": real})
    for version in ("0.8.2-aaaa", "0.8.3-bbbb"):
        home = tmp_path / "state" / version / "home"
        home.mkdir(parents=True)
        windows_user_folders.prepare_user_folder_links(home)
        windows_user_folders.prepare_user_folder_links(home)
        assert (home / "Desktop").samefile(real)
        assert (home / "Desktop" / "drawing.png").read_bytes() == b"operator file"
        assert not (home / "AppData").exists()
    assert (real / "drawing.png").read_bytes() == b"operator file"


@pytest.mark.parametrize("existing", ["file", "directory", "wrong_junction"])
def test_existing_user_data_is_never_replaced(tmp_path, monkeypatch, existing):
    import _winapi

    real = tmp_path / "real"
    real.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    alias = home / "Desktop"
    if existing == "file":
        alias.write_text("keep")
    elif existing == "directory":
        alias.mkdir()
        (alias / "keep.txt").write_text("keep")
    else:
        other = tmp_path / "other"
        other.mkdir()
        _winapi.CreateJunction(str(other), str(alias))
    monkeypatch.setattr(windows_user_folders, "known_user_folders", lambda: {"Desktop": real})
    with pytest.raises(OSError, match="must be preserved"):
        windows_user_folders.prepare_user_folder_links(home)
    assert alias.exists()
    if existing == "directory":
        assert (alias / "keep.txt").read_text() == "keep"
    elif existing == "file":
        assert alias.read_text() == "keep"
    else:
        assert alias.samefile(other)


def test_native_known_desktop_matches_windows_shell():
    import ctypes

    buffer = ctypes.create_unicode_buffer(32768)
    # Independent legacy Windows shell lookup for CSIDL_DESKTOPDIRECTORY.
    result = ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, buffer)
    assert result == 0
    assert windows_user_folders.known_user_folders()["Desktop"].samefile(Path(buffer.value))
