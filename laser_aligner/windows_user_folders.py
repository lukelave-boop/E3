"""Keep native file dialogs usable inside a private application profile."""
from __future__ import annotations

import os
import sys
from pathlib import Path

_FOLDERS = {
    "Desktop": "B4BFCC3A-DB2C-424C-B029-7FE99A87C641",
    "Documents": "FDD39AD0-238F-46AF-ADB4-6C85480369C7",
    "Downloads": "374DE290-123F-4565-9164-39C4925E467B",
    "Pictures": "33E28130-4E1E-4676-835A-98395C3BC3BB",
    "Music": "4BD8D571-6D19-48D3-BE97-422220080E43",
    "Videos": "18989B1D-99B5-455B-841C-AB7C74E4DDFC",
}


def known_user_folders() -> dict[str, Path]:
    """Resolve the signed-in user's actual folders, including OneDrive redirects."""
    import ctypes
    import uuid

    shell = ctypes.WinDLL("shell32", use_last_error=True)
    get_folder = shell.SHGetKnownFolderPath
    get_folder.argtypes = (ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p,
                           ctypes.POINTER(ctypes.c_void_p))
    get_folder.restype = ctypes.c_long
    free = ctypes.WinDLL("ole32").CoTaskMemFree
    free.argtypes = (ctypes.c_void_p,)
    free.restype = None
    result = {}
    for name, identifier in _FOLDERS.items():
        guid = ctypes.create_string_buffer(uuid.UUID(identifier).bytes_le)
        value = ctypes.c_void_p()
        status = get_folder(guid, 0, None, ctypes.byref(value))
        try:
            if status < 0 or not value.value:
                raise OSError(f"Cannot resolve the Windows {name} folder (HRESULT {status})")
            result[name] = Path(ctypes.wstring_at(value.value))
        finally:
            if value.value:
                free(value)
    return result


def prepare_user_folder_links(private_home: Path) -> None:
    """Expose user documents, never AppData or configuration, in every test home."""
    if sys.platform != "win32":
        return
    import _winapi

    for name, target in known_user_folders().items():
        # An actually unavailable user folder is not an isolation alias to invent.
        if not target.is_dir():
            continue
        alias = private_home / name
        if os.path.lexists(alias):
            if alias.is_dir() and alias.samefile(target):
                continue
            raise OSError(f"Cannot map {name}: existing path must be preserved: {alias}")
        # Native junction creation needs no elevated symlink privilege or shell.
        _winapi.CreateJunction(str(target), str(alias))
        if not alias.is_dir() or not alias.samefile(target):
            raise OSError(f"The {name} folder link did not resolve to {target}")
