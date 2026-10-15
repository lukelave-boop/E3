"""Operator wording for the wizard; these links only reveal existing tools."""

from html import escape

from ..setup_workflow import SETUP_STEPS

# Internal step IDs stay stable in saved records. Only presentation changes.
STEP_HELP = {
    "mechanics": ("Set up the camera", "Keep the camera fixed and the picture sharp.", (
        "Secure the camera, cable and honeycomb so they cannot shift.",
        "Connect the camera. Set its resolution and lock its focus in the main Camera panel; restart if you change the saved profile.",
        'Use <b>Refresh raw preview</b> and <b>Apply all configured controls</b> below. Check that the whole working area is visible and sharp.',
    )),
    "lens": ("Correct camera lens distortion", "Teach E3 how the lens bends straight lines.", (
        "Use a printed checkerboard with the dimensions entered below. Keep the camera fixed.",
        "Move and tilt the checkerboard to cover the center, edges and corners. Use <b>Capture checkerboard view</b> for each sharp view.",
        "Use <b>Solve current-resolution calibration</b>. Follow the result's instructions if more pictures are needed.",
    )),
    "bed": ("Match the picture to the bed", "Make positions in the camera picture match positions on the machine.", (
        "Secure a flat sacrificial sheet covering the displayed targets. Use <b>Prepare powered base-map job</b>; review its Preview before explicitly starting it with a previously tested marking power.",
        "Capture and review the completed grid using the controls below, then accept the map only after checking the detections.",
        'Use <a href="machine_setup.bed_mapping">the honeycomb frame controls</a> to detect and save the actual border. '
        'Then use <a href="machine_setup.fine_registration">Fine Registration</a> and '
        '<a href="machine_setup.accuracy_validation">Accuracy Validation</a> to check the alignment.',
    )),
    "datum": ("Level the bed and save honeycomb height", "Save where the bare honeycomb sits below the black border. This height is sometimes called the datum.", (
        'Use <a href="machine_setup.focus_reference">the border reference controls</a>. Check the physical headroom and clear path before explicitly using <b>Home / park + reference</b> or <b>Reference border</b>.',
        'Open <a href="tool:datum">Bed leveling measurements</a>. Enter the measured thickness of a rigid, flat block. Measure that block at four corners and the center, recording each new reading in that window.',
        'Review the five bed heights. After any manual leveling adjustment, repeat all five readings. In the measurements window, choose <b>Copy average to Honeycomb height</b>.',
        'Use <a href="machine_setup.honeycomb_height">Save honeycomb height</a> below to save the reviewed value. Then click <b>Check progress</b>. Copying the average alone does not save it.',
    )),
    "gauge": ("Set the laser focus with the 7 mm gauge", "Teach E3 the laser position that gives a 7 mm gap above the measured surface.", (
        'Finish Step 4 first. Put a rigid, flat target on the bed. Use <a href="machine_setup.focus_probe">Position probe</a> to select a solid patch, then <a href="machine_setup.focus_measure">Measure surface</a> to take a new reading.',
        'Use <b>Return laser to measured spot</b> beside Measure surface. Wait for that move to finish.',
        'Use <a href="machine_setup.focus_teach">the 7 mm gauge controls</a>. Remove the gauge before each Z move; approach in small steps, checking the fit between moves.',
        'When the gauge fits, tick <b>7 mm gauge fits at this Z</b>, then explicitly choose <b>Save current Z as 7 mm gap</b>. Click <b>Check progress</b>.',
    )),
    "precision": ("Check repeated camera positioning", "Check whether repeated pictures and Home / park cycles give consistent positions.", (
        'Use <a href="machine_setup.accuracy_validation">Accuracy Validation</a> to prepare, review and explicitly run its test marks on a restrained sacrificial target.',
        'Open <a href="tool:precision">Repeated-picture check</a>. Choose <b>Start a new assessment</b>, then <b>Establish photo pose — Home / park capture</b>.',
        'Take ten pictures with <b>Capture without movement</b>, then ten with <b>Home / park and capture</b>. Keep the target fixed. Review the reported spread; this checks consistency, not actual laser accuracy.',
    )),
    "heights": ("Correct placement at different material heights", "Account for the way a raised surface appears at a different position in the camera picture.", (
        'Open <a href="tool:heights">Material height calibration</a> and begin a new calibration using the same fixed camera and border reference.',
        'Collect a lower and an upper target at measured heights spanning the range you intend to use, plus a separate middle-height target. For each: prepare, review and explicitly run the marks, capture them, review the detections, then save that capture.',
        'Use <b>Fit and check camera model</b>. Use <b>Use accepted height model</b> only after the check passes. The window asks for height above the reference, including supports, and how precisely you measured it.',
    )),
    "qualification": ("Check actual laser placement accuracy", "Measure real laser marks to find out how accurately the machine places them.", (
        'Open <a href="tool:qualification">Laser placement measurements</a>. Check the tested bed area and lower, middle and upper heights.',
        'Measure the actual marks against independent known positions at the corners, edges and center. Enter the X/Y errors and your measuring method’s uncertainty (how far its reading could be off). Do not use the same camera map to check itself.',
        'Save the measurements and review the result. The 0.10 mm target includes both placement error and measurement uncertainty; completing the wizard does not prove that accuracy.',
    )),
}


def step_name(step_id):
    index = next(i for i, step in enumerate(SETUP_STEPS) if step.id == step_id)
    return f"Step {index + 1} — {STEP_HELP[step_id][0]}"


def instruction_html(step_id):
    _, goal, instructions = STEP_HELP[step_id]
    return f"<b>{escape(goal)}</b><ol>" + "".join(f"<li>{line}</li>" for line in instructions) + "</ol>"


def progress_text(step, status):
    if status is None:
        return "Click Check progress to check the saved settings and measurements for this step."
    if "Current gauge status is unavailable" in status.reason:
        return "Current gauge status is unavailable. This does not mean the saved calibration is lost. Check the machine connection and click Check progress; do not repeat gauge teaching."
    if "reports no compatible gauge calibration" in status.reason:
        return "The controller reports a gauge compatibility problem. Inspect Troubleshooting details before changing the saved calibration."
    if status.next_action != step.action:
        required = next(item for item in SETUP_STEPS if item.action == status.next_action)
        return f"Finish {step_name(required.id)} first. Use the button below to go there."
    if status.state == "complete":
        return "The required saved result is current. Continue to the next step when you have reviewed it."
    if status.state == "stale":
        return "The saved result no longer matches the current setup. Repeat this step, save the new result, then click Check progress."
    if status.state == "review":
        if step.id == "mechanics":
            return "The camera is ready. Check the mounting and picture yourself, then continue to Step 2."
        return "E3 cannot confirm the saved result right now. Check the machine connection, refresh its status and click Check progress."
    return "Not finished yet. Follow the numbered instructions, save the result, then click Check progress."
