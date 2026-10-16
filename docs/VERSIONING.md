# E3 versioning

E3 uses a human-readable `major.minor.patch` version plus the exact Git revision.

The current series uses the immutable Git tag `v0.8.0` as its baseline. The
patch number is the count from `git rev-list --count v0.8.0..HEAD`: `0.8.1`,
`0.8.2`, and so on. This includes merged feature commits and merge commits,
not only commits on main's first-parent history. Release builds fetch full
history and tags; a checkout without the baseline falls back to `0.8.0`.

For the first 0.8 release, verify the integration candidate before merging.
The existing publisher tests the exact final main revision and builds both
platform packages as `0.8.0` using the missing-tag fallback. After the Windows
installer, both embedded package identities and staged update manifest agree,
the publication job establishes `v0.8.0` on that exact main revision before
switching the public manifest. The untagged candidate and tagged release both
resolve to `0.8.0`; tagging a pre-merge branch commit would incorrectly count
the subsequent merge in the patch.

The baseline step uses only the publication job's existing repository-content
write permission. Before first creation, the currently published development
manifest must unambiguously identify the previous 0.7 series in this repository
and channel. Missing, malformed or current/newer-series publication evidence
blocks bootstrap, preventing a deleted established baseline from silently
resetting future versions to `0.8.0`. The step creates an absent tag atomically,
verifies the remote target, and never moves or deletes an existing tag. An existing baseline must
resolve to the exact initial release revision for `0.8.0`; later releases must
descend from that baseline and match its commit-count-derived patch version.
Any baseline tag already present in the publisher checkout must peel to the same
commit as the remote tag; missing local tags are allowed, but conflicting or
malformed local references fail closed.
A conflicting tag, moved main revision, mismatched package identity or missing
history blocks publication. Creation races are re-read and accepted only when
the resulting target and identities are correct. A baseline created before a
later publication failure remains immutable and can be reused for the same
revision; it does not by itself claim successful publication. If that tag is
externally deleted before any 0.8 publication succeeds, the old channel alone
cannot reconstruct the unpublished baseline; do not delete release tags.

The missing-tag fallback supports source archives and initial release
preparation. It is not permission to publish subsequent commits as `0.8.0` or
proof that a release is verified.

Frozen applications use their packaged `build-info.json`, so an existing
executable retains its original version when source history or the current
series changes. The previous 0.7 series used `v0.7.0`; the 0.6 series used
revision `76a7e4b193bee16008bc4bb1ee9893048ca1e586` as its baseline.

The Git revision remains visible separately as the build identifier.
`E3_BUILD_VERSION` can override the computed value for an intentional release.
If used, the same value must reach both platform builds and manifest generation.
The main publisher rejects any override that differs from the immutable
baseline-derived version; do not leave a fixed override in subsequent builds. Verify that the
Windows installer version, both packaged `build-info.json` files and the update
manifest agree on the release version, and that all metadata identifies the
exact release revision. The version string does not replace compatibility,
packaging or artifact verification.
