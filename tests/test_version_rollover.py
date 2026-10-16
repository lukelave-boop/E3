"""Keep the 0.8 release reachable from installed older development builds."""

from __future__ import annotations

import pytest

from laser_aligner.deployment import BuildInfo
from laser_aligner.updates import UpdateManifest, _update_available


@pytest.mark.parametrize(
    "current, published, available",
    [
        ("0.7.153", "0.8.0", True),
        ("0.7.999999", "0.8.0", True),
        ("0.8.0", "0.7.999999", False),
        ("0.8.0", "0.8.1", True),
        ("0.8.1", "0.8.0", False),
    ],
)
def test_updater_orders_the_new_series_before_revision_identity(
    current: str, published: str, available: bool,
) -> None:
    build = BuildInfo(
        schema_version=1, version=current, revision="a" * 40,
        channel="development", repository="example/E3",
        manifest_url="https://example.invalid/update-manifest.json",
        platform_key="windows-x86_64", packaged=True,
    )
    manifest = UpdateManifest(
        schema_version=1, version=published, revision="b" * 40,
        channel="development", published_at="2026-10-16T00:00:00+00:00", assets={},
    )
    assert _update_available(build, manifest) is available
