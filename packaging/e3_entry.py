from __future__ import annotations

import os

from laser_aligner.deployment import (
    load_build_info,
    read_bridge_token,
    resolve_launch_profile,
)
from laser_aligner.identity import (
    REVISION_ENVIRONMENT_VARIABLE,
    VERSION_ENVIRONMENT_VARIABLE,
)


def run() -> int:
    build = load_build_info()
    os.environ[REVISION_ENVIRONMENT_VARIABLE] = build.revision
    os.environ[VERSION_ENVIRONMENT_VARIABLE] = build.version
    # Select independent state before reading any persisted credential.
    profile = resolve_launch_profile()
    token = read_bridge_token()
    if token:
        os.environ.setdefault("E3_BRIDGE_TOKEN", token)
    arguments = ["--config", str(profile.config_path)]
    from laser_aligner.desktop.main import main

    return main(arguments)


if __name__ == "__main__":
    raise SystemExit(run())
