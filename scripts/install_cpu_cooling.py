"""Install the exact CPU cooling companion; default is read-only."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

MANIFEST_SHA256 = "BUILD_REPLACES_MANIFEST_SHA256"


def canonical(data):
    data.decode("utf-8")
    if data.count(b"\r\n") not in (0, data.count(b"\n")):
        raise ValueError("Preserving mixed source line endings")
    result = data.replace(b"\r\n", b"\n")
    if b"\r" in result:
        raise ValueError("Unsupported line endings")
    return result


def checked_path(root, relative):
    path = root / relative
    if path.resolve() != path or path.is_symlink() or not path.is_relative_to(root):
        raise ValueError(f"Preserving redirected source path: {path}")
    return path


def inactive():
    if not sys.platform.startswith("linux"):
        raise ValueError("--apply requires Linux")
    status = subprocess.run(
        ["systemctl", "show", "e3-hardware-node.service", "--property=ActiveState", "--value"],
        check=False, capture_output=True, text=True, timeout=5,
    )
    if status.returncode or status.stdout.strip() != "inactive":
        raise ValueError("Stop e3-hardware-node.service and clear any failed state before applying")


def install(project, *, bundle=None, apply=False):
    bundle = Path(__file__).parent if bundle is None else bundle
    manifest_bytes = (bundle / "manifest.json").read_bytes()
    if hashlib.sha256(manifest_bytes).hexdigest() != MANIFEST_SHA256:
        raise ValueError("Bundle manifest checksum mismatch")
    entries = json.loads(manifest_bytes)["files"]
    project = project.expanduser().resolve(strict=True)
    if not (project / "laser_aligner/machine").is_dir():
        raise ValueError("Expected the existing E3 project")
    prepared = []
    for entry in entries:
        relative = entry["path"]
        source = canonical(checked_path(bundle.resolve(), relative).read_bytes())
        if hashlib.sha256(source).hexdigest() != entry["sha256_lf"]:
            raise ValueError(f"Bundled source checksum mismatch: {relative}")
        target = checked_path(project, relative)
        if target.exists() and (not target.is_file() or target.stat().st_size > 1048576):
            raise ValueError(f"Unexpected source file: {target}")
        original = target.read_bytes() if target.exists() else None
        digest = hashlib.sha256(canonical(original)).hexdigest() if original is not None else None
        if digest not in [entry["previous_sha256_lf"], entry["sha256_lf"]]:
            raise ValueError(f"Preserving unknown local changes: {relative} SHA256={digest}")
        prepared.append((target, original, source, digest == entry["sha256_lf"]))
    if apply:
        inactive()
    results = []
    for target, original, source, current in prepared:
        result = {"path": str(target), "status": "already_current" if current else "ready_to_apply"}
        if apply and not current:
            checked_path(project, target.relative_to(project))
            if (target.read_bytes() if target.exists() else None) != original:
                raise ValueError(f"Source changed during preparation: {target}")
            newline = b"\r\n" if original is not None and b"\r\n" in original else b"\n"
            mode = stat.S_IMODE(target.stat().st_mode) if original is not None else 0o644
            if original is not None:
                with tempfile.NamedTemporaryFile(prefix=target.name + ".e3-backup-", suffix=".py",
                                                 dir=target.parent, delete=False) as backup:
                    backup.write(original)
                    backup.flush()
                    os.fsync(backup.fileno())
                os.chmod(backup.name, mode)
                result["backup"] = backup.name
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(prefix=target.name + ".e3-new-", dir=target.parent,
                                                 delete=False) as stream:
                    temporary = Path(stream.name)
                    stream.write(source.replace(b"\n", newline))
                    stream.flush()
                    os.fsync(stream.fileno())
                os.chmod(temporary, mode)
                checked_path(project, target.relative_to(project))
                if (target.read_bytes() if target.exists() else None) != original:
                    raise ValueError(f"Source changed before replacement: {target}")
                os.replace(temporary, target)
                temporary = None
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            result["status"] = "updated"
        results.append(result)
    return {"applied": apply, "files": results, "service_started": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(install(args.project, apply=args.apply), indent=2))
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print(f"CPU cooling support not installed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
