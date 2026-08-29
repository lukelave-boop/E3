# GitHub workflow

## First push

Create an empty repository on GitHub, then from the extracted project:

```bash
./bootstrap-git.sh
git remote add origin git@github.com:YOUR_USERNAME/YOUR_REPOSITORY.git
git push -u origin main
```

For HTTPS instead of SSH, use the repository HTTPS remote and authenticate through the Git credential flow.

## Branches

Read `AGENTS.md` and `CURRENT_STATE.md`, then inspect `git status --short` before
creating or switching branches. Preserve active uncommitted work.

Use one purpose per branch:

```bash
git checkout -b feature/c920-bringup
git checkout -b feature/controller-profile
git checkout -b fix/svg-arc-placement
```

## Sharing changes for review here

The most useful items are a repository ZIP, a public repository link, a unified diff from `git diff`, or the exact changed files. Include logs and screenshots when a defect depends on the physical rig.

## CI validation tiers

Fast Development CI runs on pushes to `fix/**`, `feature/**`, `agent/**`,
`cleanup/**`, and `architecture/**`. It runs three parallel Windows Python 3.12
jobs: repository Ruff, desktop dependency plus bytecode validation, and the
complete desktop-enabled pytest suite with four bounded xdist workers. It is the
normal post-push confidence check after focused local testing.

Compatibility CI runs on pushes to `main`, pull requests targeting `main`, and
manual dispatch. It runs serial pytest on Windows Python 3.10 without desktop
extras and Windows Python 3.12 with desktop extras, plus a separate repository
Ruff job. Use this tier before merge or release. Linux/Pi-specific components
retain focused verification when changed, but there is no standing Ubuntu
compatibility matrix. Do not merge merely to obtain development feedback.

Each timed command writes its elapsed duration to the Actions job summary. The
fast suite uses bounded `pytest -n 4`; the full compatibility matrix remains
serial to retain an independent check against shared-state assumptions.

## Files intentionally ignored

- `config/local.json`
- camera captures and calibration photos
- generated G-code
- logs
- virtual environments and Python caches

Camera/object-trace inputs, trace previews, and trace-result JSON are also local
artifacts unless they are deliberately reviewed and added as stable test
fixtures. The release tool archives tracked regular files from the current
checkout, including uncommitted edits to those files. It rejects tracked
symbolic links and never adds untracked files; still review the resulting ZIP
before publishing it.

After finalizing the rig, back up the useful calibration JSON files outside Git or add a sanitized machine-profile export feature rather than committing arbitrary images and logs.
