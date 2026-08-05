# Contributing

Use focused branches and keep real-machine changes separate from camera/UI changes.

```bash
git checkout -b feature/descriptive-name
python -m pytest
git add .
git commit -m "Describe the tested change"
```

For controller-related changes, include the controller identity, firmware version, protocol, relevant configuration output, and whether the laser power lead was physically disconnected during testing. Never commit personal access tokens, private network credentials, serial numbers you consider sensitive, `config/local.json`, or captured work images without reviewing them.

Pull requests should state what was tested in simulation, what was tested on hardware, and what remains unverified. New motion or laser-output paths require unit tests for arming, bounds checks, and an `M5` failure path.
