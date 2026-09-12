"""Check or install the exact compact-updater readiness companion on the Pi.

Default operation is read-only. --apply changes one known source file, keeps a
unique backup, and requires the E3 service to be inactive. It never opens serial,
restarts a service, installs dependencies, or changes Git branches/history.
The reader remains compatible with existing owners; explicit Ender recovery
requires the complete Pi laser-focus companion, not this single-file update.
"""

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

RELATIVE_TARGET = Path("laser_aligner/machine/secondary_startup.py")
DESIRED_SHA256 = "d7c6bc19862d8f3dd5fd638581860057189956e4314157efb92f317298eb258b"
# These are SHA-256 hashes of UTF-8 source with LF line endings. Uniform CRLF
# copies are accepted too, and the project's existing line endings are retained.
ACCEPTED_SOURCE_SHA256 = {
    "dd2ba0f981681e6be1479e061746418922b417ef06712c82fef24e4c230890a9": "startup-fixed F401 source",
    "d4075d31c2551961999f918165ae2940ab4dcf28244fa81c443917ca226fd280": "F103 companion source",
    "b052d29029c2d393014cd252ac70de56b01178ee8249a1f9002b5a0ddcfec0d2": "original compact F401 companion source",
    DESIRED_SHA256: "current compact F401 readiness reader",
}
SERVICE = "e3-hardware-node.service"


class SupportError(ValueError):
    """The exact source or inactive-service preconditions were not satisfied."""


def read_source(path: Path) -> tuple[bytes, bytes, bytes]:
    if not path.is_file() or path.stat().st_size > 65536:
        raise SupportError(f"Expected a regular source file no larger than 64 KiB: {path}")
    with path.open("rb") as stream:
        original = stream.read(65537)
    if len(original) > 65536:
        raise SupportError("Source changed size during inspection")
    try:
        original.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise SupportError("Source is not UTF-8") from exc
    crlf = original.count(b"\r\n")
    if crlf and crlf != original.count(b"\n"):
        raise SupportError("Preserving source with mixed line endings")
    canonical = original.replace(b"\r\n", b"\n")
    if b"\r" in canonical:
        raise SupportError("Preserving source with unsupported line endings")
    return original, canonical, b"\r\n" if crlf else b"\n"


def service_inactive() -> None:
    if not sys.platform.startswith("linux"):
        raise SupportError("--apply is supported only on the Linux Pi; check-only is portable")
    try:
        result = subprocess.run(
            ["systemctl", "show", SERVICE, "--property=ActiveState", "--value"],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SupportError("Could not confirm that the E3 service is inactive") from exc
    if result.returncode != 0 or result.stdout.strip() != "inactive":
        raise SupportError(f"Stop {SERVICE} before --apply; its inactive state was not confirmed")


def target_path(project: Path) -> Path:
    project = project.expanduser().resolve(strict=True)
    if not project.is_dir():
        raise SupportError("--project must name the existing editable project directory")
    target = project / RELATIVE_TARGET
    # Replacing an ordinary file must not redirect through any symlink, even if
    # an editable project contains links to another checkout or configuration.
    if target.is_symlink() or target.resolve(strict=True) != target:
        raise SupportError("Preserving a source path that resolves through a symlink")
    return target


def install_support(project: Path, *, apply: bool = False, source: Path | None = None) -> dict:
    desired_path = source if source is not None else Path(__file__).with_name("secondary_startup.py")
    _bundled, desired, _newline = read_source(desired_path)
    if hashlib.sha256(desired).hexdigest() != DESIRED_SHA256:
        raise SupportError("Bundled secondary_startup.py failed its pinned SHA-256 check")
    target = target_path(project)
    original, current, newline = read_source(target)
    digest = hashlib.sha256(current).hexdigest()
    if digest not in ACCEPTED_SOURCE_SHA256:
        raise SupportError(f"Preserving unknown local edits in {target}; SHA-256 {digest}")
    result = {
        "status": "already_current" if current == desired else "ready_to_apply",
        "target": str(target), "source_sha256_lf": digest,
        "desired_sha256_lf": DESIRED_SHA256, "changed": False,
    }
    if not apply:
        return result
    service_inactive()
    if current == desired:
        return result

    mode = stat.S_IMODE(target.stat().st_mode)
    replacement = desired.replace(b"\n", newline)
    temporary: Path | None = None
    backup: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=target.name + ".e3-new-", dir=target.parent, delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(replacement)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        # Repeat the source check immediately before backup and replacement;
        # preserve edits made while the service check or file preparation ran.
        if target_path(project) != target or read_source(target)[0] != original:
            raise SupportError("Source changed during preparation; preserving the new contents")
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=target.name + ".e3-backup-", suffix=".py",
            dir=target.parent, delete=False,
        ) as stream:
            backup = Path(stream.name)
            stream.write(original)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(backup, mode)
        if target_path(project) != target or read_source(target)[0] != original:
            raise SupportError("Source changed before replacement; preserving the new contents and backup")
        os.replace(temporary, target)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    result.update(status="updated", changed=True, backup=str(backup))
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--apply", action="store_true", help="Apply only to an accepted source with E3 service inactive")
    args = parser.parse_args(argv)
    try:
        result = install_support(args.project, apply=args.apply)
    except (OSError, SupportError) as exc:
        print(f"Pi support was not installed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
