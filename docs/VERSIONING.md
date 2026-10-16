# E3 versioning

E3 uses a human-readable `major.minor.patch` version plus the exact Git revision.

The current series uses the immutable Git tag `v0.8.0` as its baseline. The
patch number is the count from `git rev-list --count v0.8.0..HEAD`: `0.8.1`,
`0.8.2`, and so on. This includes merged feature commits and merge commits,
not only commits on main's first-parent history. Release builds fetch full
history and tags; a checkout without the baseline falls back to `0.8.0`.

For the first 0.8 release, verify the integration candidate before merging,
then create `v0.8.0` on the exact final main release revision before the
publisher's packaging jobs check out that revision. The untagged candidate and
that tagged release both resolve to `0.8.0`; tagging a pre-merge branch commit
would instead count the subsequent merge in the patch. Verify the tag target
and do not move an existing release tag. The missing-tag fallback supports
source archives and release preparation; it does not prove a release is verified.

Frozen applications use their packaged `build-info.json`, so an existing
executable retains its original version when source history or the current
series changes. The previous 0.7 series used `v0.7.0`; the 0.6 series used
revision `76a7e4b193bee16008bc4bb1ee9893048ca1e586` as its baseline.

The Git revision remains visible separately as the build identifier.
`E3_BUILD_VERSION` can override the computed value for an intentional release.
If used, the same value must reach both platform builds and manifest generation;
do not leave a fixed override in subsequent development builds. Verify that the
Windows installer version, both packaged `build-info.json` files and the update
manifest agree on the release version, and that all metadata identifies the
exact release revision. The version string does not replace compatibility,
packaging or artifact verification.
