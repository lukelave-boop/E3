# Editing numbers in E3

Numeric fields in the desktop tabs and dialogs accept ordinary text editing:
select and delete the whole number, replace one digit, or type a decimal such
as `0.1`. Temporary text such as an empty field, `-`, `.`, or a number below
the minimum is allowed while entering the final value. For example, entering
`40` in the Z maximum field must pass through `4` without dropping that digit.

Press Enter, Tab, or leave the field to commit a valid number. Fields with an
Apply or Save action still need that action to persist the setting. Incomplete
or out-of-range text restores the previous valid value on leaving the field;
it does not silently clamp the draft to a different number. Arrow controls
step from the committed value, discarding an invalid draft first. Displayed
precision is applied at commit, after checking the unrounded value against
the field's bounds.

Measurement fields continue to accept explicit units, such as `0.1 in`.
Speed percentages continue to preserve their underlying saved feed values.
The Z maximum editor keeps its draft across live status refreshes; changing
controller sessions replaces it with the newly reported machine setting.
Its Apply button checks the full draft before submitting the maximum.

Mirroring, aspect locking, stock layout and template nudge buttons commit the
focused number before acting. Saving or generating a project also finishes
the current project numeric edit, including queued layer property changes,
so the saved file or generated job uses the value you just entered.

The browser's measurement inputs already allowed incomplete text. Its scalar
power, rotation and baud-rate inputs now reject incomplete or non-finite text
at submission rather than interpreting an empty field as zero.

This is a desktop/browser editing change. It does not require a Pi companion
or firmware update, and does not change accepted machine limits, controller
ownership, motion permissions, or laser arming.

## Verification and operator check

Windows offscreen Qt tests send actual keyboard and focus events through the
shared controls, representative tabs/dialogs and Z maximum editor. They cover
full deletion, first-digit replacement, decimal entry, units, integer fields,
invalid commits, arrow stepping, live refresh and session changes. Browser
parser tests execute the JavaScript with Node.js. These are automated widget
and parser checks, not a live hardware test.

Open the selected build with **E3 DEV TEST**. In an enabled numeric field,
clear the number and type the replacement, then press Tab. Try `80.0` to `40`
in Set max, and `0.1` in a decimal field. Set max changes the active machine
limit only when Apply is used. Confirm that an unfinished edit remains in
place while Z status refreshes.
