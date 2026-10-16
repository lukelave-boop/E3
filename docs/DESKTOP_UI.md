# Desktop UI guide

## Choose and change options

Dropdowns have a separate arrow button. Number fields have visible up/down
steppers. Hover highlights an available control; a brighter outline identifies
keyboard focus. Selected tools and tabs have a filled highlight and border.
Disabled controls retain a quieter appearance. The **Panels** menu lists every
inspector, including tabs hidden by a narrow sidebar. Tab and Shift+Tab still navigate
controls, and keyboard operation retains the native Qt behavior.

The drawing rail shows names beside its icons. Capture, Generate and Preview
also show names. Existing menu commands and shortcuts remain available.

The Cuts panel labels its **Layer** and **Operation** selectors. **Add**, **Remove**,
**Up** and **Down** describe layer management. **Include in job** controls generated
output; **Show on canvas** controls workspace visibility. These settings retain
their existing behavior and do not start a job.

## Image editing

Open the engraving editor from the selected image's controls. Its workflow groups
image cleanup, outline editing and optional vector output. Named tools show the
available editing choices, with instructions for the selected tool. Preview and
accepted outline states remain separate; use the explicit preview acceptance
action before committing. **Apply to project** commits the draft as one project
edit; **Cancel** discards the draft. Original image bytes remain unchanged.

## Design references

- [Microsoft: combo boxes and list boxes](https://learn.microsoft.com/en-us/windows/apps/develop/ui/controls/combo-box): use visible alternatives for important choices and small sets; use labelled dropdowns for compact secondary choices.
- [Nielsen Norman Group: recognition rather than recall](https://www.nngroup.com/articles/recognition-and-recall/): keep useful options and context visible so users do not have to remember hidden tools.
- [Qt: stylesheet examples](https://doc.qt.io/qt-6/stylesheet-examples.html): custom combo boxes and spin boxes explicitly style their subcontrols and indicators.

These references informed E3's existing native Qt interface; no external
application assets, branding or controller behavior were copied. Offscreen
screenshots are UI evidence, not physical hardware qualification. Verification
and operator-test status are recorded in [CURRENT_STATE](../CURRENT_STATE.md).
