"""Verify the exact identity of packaged release metadata before publication."""

from __future__ import annotations

import argparse
import json
import math
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from version_for_build import build_version


class MetadataError(ValueError):
    """A release input does not identify the expected build."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise MetadataError(f"duplicate JSON field {key!r}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise MetadataError(f"invalid JSON constant {value}")


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise MetadataError(f"nonfinite JSON number {value}")
    return number


def _read_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
            parse_float=_finite_float,
        )
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        raise MetadataError(f"{path}: cannot read valid metadata: {exc}") from exc
    if not isinstance(payload, dict):
        raise MetadataError(f"{path}: metadata must be a JSON object")
    return payload


def _expected_text(value: str, name: str) -> None:
    if not isinstance(value, str) or not value or value != value.strip():
        raise MetadataError(f"{name} must be a nonempty string without surrounding whitespace")


def _require_field(payload: dict[str, Any], field: str, expected: Any, path: Path) -> None:
    if field not in payload:
        raise MetadataError(f"{path}: missing {field}")
    actual = payload[field]
    if type(actual) is not type(expected) or actual != expected:
        raise MetadataError(f"{path}: {field} must be {expected!r}; found {actual!r}")


def verify_release_metadata(
    build_infos: Sequence[tuple[str, Path]],
    *,
    revision: str,
    channel: str = "development",
    version: str | None = None,
    manifest: Path | None = None,
    installer_version: str | None = None,
) -> str:
    """Check every supplied file without modifying files or normalizing identities."""

    if not isinstance(revision, str) or re.fullmatch(r"[0-9a-fA-F]{40}", revision) is None:
        raise MetadataError("revision must be exactly 40 hexadecimal characters")
    expected_version = build_version() if version is None else version
    _expected_text(expected_version, "version")
    _expected_text(channel, "channel")
    if not build_infos:
        raise MetadataError("at least one --build-info PLATFORM PATH is required")
    identity = {"schema_version": 1, "version": expected_version, "revision": revision, "channel": channel}
    for platform, path in build_infos:
        _expected_text(platform, "platform")
        payload = _read_object(path)
        for field, expected in {**identity, "platform_key": platform, "packaged": True}.items():
            _require_field(payload, field, expected, path)
    if manifest is not None:
        payload = _read_object(manifest)
        for field, expected in identity.items():
            _require_field(payload, field, expected, manifest)
    if installer_version is not None:
        _expected_text(installer_version, "installer ProductVersion")
        if installer_version != expected_version:
            raise MetadataError(
                f"installer ProductVersion must be {expected_version!r}; found {installer_version!r}"
            )
    return (
        f"verified version={expected_version} revision={revision} channel={channel} "
        f"build_info={len(build_infos)} manifest={'yes' if manifest is not None else 'no'} "
        f"installer={'yes' if installer_version is not None else 'no'}"
    )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--build-info", nargs=2, action="append", metavar=("PLATFORM", "PATH"), default=[])
    result.add_argument("--revision", required=True)
    result.add_argument("--channel", default="development")
    result.add_argument("--version", help="Expected version; defaults to the shared build version")
    result.add_argument("--manifest", type=Path)
    result.add_argument("--installer-version", help="Windows installer's exact ProductVersion string")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    argument_parser = parser()
    arguments = argument_parser.parse_args(argv)
    try:
        identity = verify_release_metadata(
            [(platform, Path(path)) for platform, path in arguments.build_info],
            revision=arguments.revision,
            channel=arguments.channel,
            version=arguments.version,
            manifest=arguments.manifest,
            installer_version=arguments.installer_version,
        )
    except MetadataError as exc:
        argument_parser.error(str(exc))
    print(identity)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
