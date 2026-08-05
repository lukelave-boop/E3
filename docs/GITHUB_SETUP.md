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

Use one purpose per branch:

```bash
git checkout -b feature/c920-bringup
git checkout -b feature/controller-profile
git checkout -b fix/svg-arc-placement
```

## Sharing changes for review here

The most useful items are a repository ZIP, a public repository link, a unified diff from `git diff`, or the exact changed files. Include logs and screenshots when a defect depends on the physical rig.

## Files intentionally ignored

- `config/local.json`
- camera captures and calibration photos
- generated G-code
- logs
- virtual environments and Python caches

After finalizing the rig, back up the useful calibration JSON files outside Git or add a sanitized machine-profile export feature rather than committing arbitrary images and logs.
