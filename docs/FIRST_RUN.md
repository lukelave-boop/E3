# E3 first-run setup

Generic packages do not contain machine-specific credentials or calibration.
On the first packaged launch, E3 opens a guided setup wizard when no preserved
machine configuration exists.

The wizard configures the Raspberry Pi/controller/camera bridge addresses,
stores the bridge credential in the per-user E3 data directory, sets the machine
work area and initial camera settings, tests bridge reachability, and then opens
Machine Setup for focus, lens calibration, and bed mapping.

Choosing offline mode defers hardware setup. `Help > Set Up Hardware...` remains
available until a machine configuration has been saved.

Machine configuration, credential, calibration, templates, materials, and
projects remain outside the replaceable application directory and survive
normal E3 updates.
