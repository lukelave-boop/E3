# Safety requirements

This repository controls equipment capable of causing permanent eye injury, fire, toxic smoke exposure, and mechanical injury. It is experimental software and is not a safety-rated control system.

## Required physical safeguards

Use a fully enclosed laser area designed for the laser wavelength and power. The enclosure should include a hardware door interlock that removes laser-enable power, a readily accessible hardware emergency stop that removes hazardous energy, appropriate extraction exhausting safely outdoors, a nonflammable spoil surface, and a suitable fire extinguisher within reach.

Do not depend on the webcam, browser, operating system, USB connection, G-code sender, or a software `M5` command for personnel protection. Any of them can freeze, disconnect, reset, or behave unexpectedly.

## Before every real job

- Confirm that the intended material is appropriate for blue-diode laser processing and does not produce prohibited or highly hazardous fumes.
- Confirm extraction flow and the exhaust destination.
- Remove flammable debris from the enclosure.
- Confirm focus, workpiece restraint, cable clearance, and unobstructed axis travel.
- Run the generated dry framing pass first; it contains no `M3` or `M4` laser-enable command.
- Verify the generated bounds and the machine coordinate origin.
- Keep the operator present for the entire job.

## Software guardrails in this repository

The default project profile is simulation-only. Real serial access requires `--hardware`; real motion requires `machine.allow_motion`; positive laser commands require temporary arming; arming expires automatically before job start and is cleared after every job; low-power framing is disabled; streamed jobs are restricted to a conservative G-code subset; generated paths are checked against a configured rectangular work area; rapid travel is blocked while laser state is active; and `M5` is placed before travel and at job end.

These controls reduce accidental commands. They do not meet any functional-safety performance level and do not make an open Class 4 laser safe.

## Camera sensor protection

The C920 is not a laser power sensor or a protective viewing system. Direct or specular reflected laser energy can damage an image sensor. Position and shield it so the lens cannot receive a direct beam or a strong specular reflection. Do not place an unverified optical filter over the camera and assume it provides personnel protection.

## Emergency behavior

In an actual emergency, use the hardware emergency stop or disconnect power. The red software stop sends controller-specific reset/stop commands and `M5`, but software and serial delivery can fail.
