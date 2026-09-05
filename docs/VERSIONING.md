# E3 versioning

E3 uses a human-readable `major.minor.patch` version plus the exact Git revision.

The current series starts at the immutable Git tag `v0.7.0`. Every commit after
that baseline increments the patch number automatically: `0.7.1`, `0.7.2`, and
so on. Release builds fetch full history and tags; a checkout without the
baseline falls back to `0.7.0`. Frozen applications use their packaged
`build-info.json`, so an existing executable does not change version when Git
history changes. The previous 0.6 series used revision
`76a7e4b193bee16008bc4bb1ee9893048ca1e586` as its baseline.

The Git revision remains visible separately as the build identifier.
`E3_BUILD_VERSION` can override the computed value for an intentional release.
