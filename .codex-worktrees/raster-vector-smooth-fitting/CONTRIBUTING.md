# Contributing

Read `AGENTS.md`, `CURRENT_STATE.md`, and `SAFETY.md` before changing code. Use
focused branches and keep real-machine changes separate from camera/UI changes.
Inspect `git status --short` first and preserve unrelated or uncommitted work.

```bash
git checkout -b feature/descriptive-name
python -m pytest
git add .
git commit -m "Describe the tested change"
```

For controller-related changes, include the controller identity, firmware version, protocol, relevant configuration output, and whether the laser power lead was physically disconnected during testing. Never commit personal access tokens, private network credentials, serial numbers you consider sensitive, `config/local.json`, or captured work images without reviewing them.

Pull requests should state what was tested in simulation, what was tested on hardware, and what remains unverified. New motion or laser-output paths require unit tests for arming, bounds checks, and an `M5` failure path.

Use precise verification language:

- **Tested** means a currently passing automated test.
- **Smoke-tested** means imported or constructed without an end-to-end flow.
- **Historically verified** applies only to an earlier commit or release.
- **Physically verified** requires identified hardware, configuration, and a
  recorded result.

Linux-only modules must not prevent the portable core or simulator from
importing on Windows. Platform-specific tests should skip clearly on unsupported
systems rather than fail during collection. Changes to shared code should report
results from both Windows and Linux when both environments are available.

Desktop source-parsing assertions are useful regression guards but do not count
as behavioral GUI verification. Report offscreen widget checks, interactive GUI
tests, real camera tests, and real controller tests separately.

Update `CURRENT_STATE.md` when a change alters platform support, verification,
known gaps, or active working-tree features. Update the README, architecture,
roadmap, and changelog when user-visible behavior changes.
