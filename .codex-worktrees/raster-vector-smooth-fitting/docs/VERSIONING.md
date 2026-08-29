# E3 versioning

E3 uses a human-readable `major.minor.patch` version plus the exact Git revision.

The current series starts at `0.6.0` on revision
`76a7e4b193bee16008bc4bb1ee9893048ca1e586`. Every commit after that baseline
increments the patch number automatically: `0.6.1`, `0.6.2`, and so on.

The Git revision remains visible separately as the build identifier.
`E3_BUILD_VERSION` can override the computed value for an intentional release.
