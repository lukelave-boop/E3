"""Read-only frozen-code/profile check; never start E3 or construct its services."""
from __future__ import annotations

import argparse
import hashlib
import importlib.abc
import importlib.util
import json
import marshal
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from types import CodeType, SimpleNamespace

PROFILE_KEYS = ("LOCALAPPDATA", "APPDATA", "USERPROFILE", "HOME", "XDG_DATA_HOME")
FOLDERS = ("HomeLocation", "DesktopLocation", "DocumentsLocation", "DownloadLocation", "PicturesLocation")
REQUIRED_MODULES = {"laser_aligner.state_scope", "laser_aligner.dev_test_launcher", "laser_aligner.deployment",
                    "laser_aligner.storage", "laser_aligner.project.io", "laser_aligner.materials.database"}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def archive_readers(exe: Path):
    from PyInstaller.archive.readers import CArchiveReader

    archive = CArchiveReader(str(exe))
    pyz = archive.open_embedded_archive("PYZ.pyz")
    with exe.open("rb") as stream:
        stream.seek(pyz._start_offset)
        require(stream.read(8) == b"PYZ\0" + importlib.util.MAGIC_NUMBER,
                "Verification requires the same Python bytecode version as the bundle")
    return archive, pyz


def git(root: Path, *arguments: str) -> bytes:
    return subprocess.check_output(["git", *arguments], cwd=root, timeout=30)


def verify_archive(root: Path, exe: Path, revision: str) -> dict:
    from verify_release_metadata import verify_release_metadata

    require(git(root, "rev-parse", "HEAD").decode().strip() == revision, "Build revision must equal checked-out HEAD")
    metadata = exe.parent / "build-info.json"
    verify_release_metadata((("windows-x86_64", metadata),), revision=revision)
    info = json.loads(metadata.read_text(encoding="utf-8"))
    archive, pyz = archive_readers(exe)

    def source(path: str) -> bytes:
        committed = git(root, "show", f"{revision}:{path}")
        require((root / path).read_bytes().replace(b"\r\n", b"\n") == committed.replace(b"\r\n", b"\n"),
                f"Checked-out source differs from the build revision: {path}")
        return committed

    verified = []
    for name, entry in sorted(pyz.toc.items()):
        if name != "laser_aligner" and not name.startswith("laser_aligner."):
            continue
        code = pyz.extract(name)
        require(isinstance(code, CodeType) and entry[0] in (0, 1), f"Unsupported archived application module: {name}")
        path = name.replace(".", "/") + ("/__init__.py" if entry[0] == 1 else ".py")
        require(code == compile(source(path), code.co_filename, "exec", dont_inherit=True, optimize=0),
                f"Archived code differs from committed source: {name}")
        verified.append(name)
    require(REQUIRED_MODULES <= set(verified), f"Required application modules missing: {REQUIRED_MODULES - set(verified)}")
    entry = marshal.loads(archive.extract("e3_entry"))
    require(entry == compile(source("packaging/e3_entry.py"), entry.co_filename, "exec", dont_inherit=True, optimize=0),
            "Archived entrypoint differs from committed source")
    assets = git(root, "ls-tree", "-r", "--name-only", revision, "laser_aligner/desktop/assets").decode().splitlines()
    resources = [path for path in assets if path.endswith(".svg")]
    resources += ["laser_aligner/operator_docs/PERMANENT_CAMERA_SETUP.md", "laser_aligner/web/app.js",
                  "laser_aligner/web/index.html", "laser_aligner/web/style.css", "config/default.json"]
    for path in resources:
        bundled = exe.parent / path if path.startswith("config/") else exe.parent / "_internal" / path
        require(bundled.read_bytes().replace(b"\r\n", b"\n") == source(path).replace(b"\r\n", b"\n"),
                f"Packaged resource differs from committed source: {path}")
    with exe.open("rb") as stream:
        digest = hashlib.sha256()
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return {"build_info": info, "exe_sha256": digest.hexdigest(), "verified_modules": verified,
            "verified_module_count": len(verified), "required_module_count": len(REQUIRED_MODULES),
            "verified_resources": resources, "verified_entrypoint": "packaging/e3_entry.py"}


def deny_side_effects(event, args) -> None:
    if event == "open":
        _path, mode, flags = args
        if (mode and any(value in mode for value in "wax+")) or flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT):
            raise RuntimeError("Profile probe forbids file writes")
    if (event.startswith("socket.") or event in {"subprocess.Popen", "os.system", "os.startfile", "os.startfile/2",
            "os.mkdir", "os.remove", "os.rename", "os.rmdir", "sqlite3.connect", "winreg.CreateKey",
            "winreg.DeleteKey", "winreg.DeleteValue", "winreg.SetValue"}):
        raise RuntimeError(f"Profile probe forbids external activity: {event}")


def child_probe(exe: Path) -> dict:
    archive, pyz = archive_readers(exe)
    internal = (exe.parent / "_internal").resolve()

    class Loader(importlib.abc.MetaPathFinder, importlib.abc.Loader):
        def find_spec(self, fullname, path=None, target=None):
            forbidden = ("laser_aligner.core", "laser_aligner.app_context", "laser_aligner.machine",
                         "laser_aligner.camera", "laser_aligner.desktop.main", "laser_aligner.server", "serial")
            if any(fullname == value or fullname.startswith(value + ".") for value in forbidden):
                raise ImportError(f"Runtime/service imports forbidden: {fullname}")
            entry = pyz.toc.get(fullname)
            if entry is not None and entry[0] in (0, 1):
                return importlib.util.spec_from_loader(fullname, self, is_package=entry[0] == 1)
            if fullname == "laser_aligner" or fullname.startswith("laser_aligner."):
                raise ImportError(f"Application module absent from archive: {fullname}")
            return None

        def create_module(self, spec):
            return None

        def exec_module(self, module):
            location = internal.joinpath(*module.__name__.split("."))
            package = pyz.toc[module.__name__][0] == 1
            module.__file__ = str(location / "__init__.py" if package else location.with_suffix(".py"))
            if package:
                module.__path__ = [str(location)]
            exec(pyz.extract(module.__name__), module.__dict__)

    sys.dont_write_bytecode = True
    sys.path.insert(0, str(internal))
    dll_handles = [os.add_dll_directory(str(path)) for path in
                   (internal, internal / "PySide6", internal / "shiboken6") if path.is_dir()]
    sys._MEIPASS, sys.frozen = str(internal), True
    os.environ.setdefault("E3_BUILD_INFO", str(exe.parent / "build-info.json"))
    sys.meta_path.insert(0, Loader())
    sys.addaudithook(deny_side_effects)
    from laser_aligner.state_scope import prepare_runtime_state

    prepare_runtime_state()  # Must precede the first Qt import.
    from PySide6 import QtCore

    require(Path(QtCore.__file__).resolve().is_relative_to(internal), "QtCore must come from the selected frozen bundle")
    app = QtCore.QCoreApplication([])
    from laser_aligner.config import _path
    from laser_aligner.deployment import bridge_token_path, load_build_info, user_config_path
    from laser_aligner.materials.database import _default_database_path
    from laser_aligner.project.io import _default_autosave_path
    from laser_aligner.storage import default_user_data_dir

    private = Path(os.environ["E3_USER_STATE_DIR"]).resolve()
    configured_data = json.loads(user_config_path().read_text(encoding="utf-8"))["app"]["data_dir"]
    paths = {"root": default_user_data_dir(), "config": user_config_path(), "credential": bridge_token_path(),
             "build_info": Path(os.environ["E3_BUILD_INFO"]),
             "data": _path(configured_data, user_config_path().parent), "materials": _default_database_path(),
             "autosave": _default_autosave_path(SimpleNamespace(name="probe", id="probe"), None)}
    require(all(path.resolve().is_relative_to(private) for path in paths.values()), "E3 paths escaped private state")
    import ctypes

    get_handle, get_name = ctypes.windll.kernel32.GetModuleHandleW, ctypes.windll.kernel32.GetModuleFileNameW
    get_handle.argtypes, get_handle.restype = [ctypes.c_wchar_p], ctypes.c_void_p
    get_name.argtypes, get_name.restype = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint], ctypes.c_uint
    dlls = {}
    for name in ("Qt6Core.dll", "pyside6.abi3.dll", "shiboken6.abi3.dll"):
        buffer = ctypes.create_unicode_buffer(32768)
        require(bool(get_name(get_handle(name), buffer, len(buffer))), f"Bundled native DLL was not loaded: {name}")
        require(Path(buffer.value).resolve().is_relative_to(internal), f"Native DLL escaped selected bundle: {name}")
        dlls[name] = buffer.value
    require(bool(app) and bool(dll_handles), "Native profile probe did not initialize")
    return {"home": str(Path.home()), "folders": {name: QtCore.QStandardPaths.writableLocation(getattr(QtCore.QStandardPaths, name)) for name in FOLDERS},
            "private_paths": {name: str(path) for name, path in paths.items()}, "qt_core": QtCore.__file__, "native_dlls": dlls,
            "build_identity": {key: getattr(load_build_info(), key) for key in ("version", "revision", "channel", "manifest_url")}}


def probe_profiles(exe: Path, scratch: Path) -> dict:
    baseline = {key: value for key, value in os.environ.items() if not key.startswith(("E3_", "_PYI_"))}
    results = {}
    for mode in ("normal", "dev-test"):
        private = (scratch / mode).resolve()
        private.mkdir()
        config = private / "config.json"
        config.write_text(json.dumps({"app": {"data_dir": str(private / "data")}}), encoding="utf-8")
        info = json.loads((exe.parent / "build-info.json").read_text(encoding="utf-8"))
        if mode == "dev-test":
            info.update(channel="dev-test", manifest_url="")
        build_info = private / "build-info.json"
        build_info.write_text(json.dumps(info), encoding="utf-8")
        environment = dict(baseline, E3_USER_STATE_DIR=str(private), E3_CONFIG_PATH=str(config),
                           E3_BRIDGE_TOKEN_FILE=str(private / "bridge-token.txt"), E3_BUILD_INFO=str(build_info),
                           PYTHONDONTWRITEBYTECODE="1")
        if mode == "dev-test":
            environment.update(E3_DEV_TEST="1", E3_STATE_ORIGINAL_ENV=json.dumps({key: baseline.get(key) for key in PROFILE_KEYS}))
            environment.update({key: str(private / "redirected" / key) for key in PROFILE_KEYS})
        try:
            process = subprocess.run([sys.executable, "-B", str(Path(__file__).resolve()), "--child", "--exe", str(exe)],
                                     env=environment, capture_output=True, text=True, check=True, timeout=60,
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.CalledProcessError as exc:
            raise ValueError(f"Frozen {mode} profile probe failed:\n{exc.stdout}\n{exc.stderr}") from exc
        results[mode] = json.loads(process.stdout)
        results[mode]["probe_stderr"] = getattr(process, "stderr", "")
        require(results[mode]["build_identity"] == {key: info[key] for key in ("version", "revision", "channel", "manifest_url")},
                "Frozen runtime changed its explicit private build identity")
    require(all(results["normal"][key] == results["dev-test"][key] for key in ("home", "folders")),
            "DEV TEST browsing folders differ from the normal Windows profile\n" + json.dumps(results, indent=2))
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", type=Path, required=True)
    parser.add_argument("--revision")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    arguments = parser.parse_args()
    if arguments.child:
        print(json.dumps(child_probe(arguments.exe.resolve())))
        return
    require(os.name == "nt" and bool(arguments.revision) and arguments.report is not None,
            "Windows verification requires --revision and --report")
    result = verify_archive(Path(__file__).resolve().parents[1], arguments.exe.resolve(), arguments.revision)
    with tempfile.TemporaryDirectory(prefix="e3-frozen-profile-") as temporary:
        result["profile_probes"] = probe_profiles(arguments.exe.resolve(), Path(temporary))
    result["canonical_exe_launched"] = False
    arguments.report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"Verified {result['verified_module_count']} archived modules, bundled QtCore and isolated Windows profiles")


if __name__ == "__main__":
    main()
