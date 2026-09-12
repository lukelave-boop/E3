// SPDX-License-Identifier: GPL-3.0-or-later
// Compiled through G30.cpp, which is already selected by HAS_BED_PROBE.
#include "../../module/e3_material_height.h"
#include "../../module/endstops.h"
#include "../../module/planner.h"
#include "../../MarlinCore.h"

// G39: exactly one native fast/retract/slow material cycle at current XY.
// Bare G39 retains V1. C<clearance> H<upper contact> selects the V2 envelope.
// No homing, coordinate shift, XY positioning, or automatic retry.
void GcodeSuite::G39() {
  const bool surface = strcmp(parser.command_ptr, "G39") != 0;
  e3_material::Envelope envelope = { e3_material::start_z, e3_material::maximum, e3_material::minimum };
  if (surface && !e3_material::surface_arguments(parser.command_ptr, envelope)) {
    SERIAL_ERROR_MSG("E3MH:2 ARGUMENTS");
    return;
  }
  if (!IsRunning() || !axis_is_trusted(Z_AXIS) || axis_is_relative(Z_AXIS)
      || e3_material::envelope_active
      || endstops.z_probe_enabled
      || !e3_material::start_ok(current_position.z, probe.offset.z,
          LOGICAL_Z_POSITION(0), Z_CLEARANCE_MULTI_PROBE,
          _MAX(Z_CLEARANCE_BETWEEN_PROBES, Z_CLEARANCE_DEPLOY_PROBE),
          envelope.clearance, envelope.maximum, envelope.minimum)
      #if HAS_LEVELING
        || planner.leveling_active
      #endif
      #if ENABLED(INCH_MODE_SUPPORT)
        || parser.linear_unit_factor != 1.0f
      #endif
  ) {
    if (surface) SERIAL_ERROR_MSG("E3MH:2 PRECONDITION");
    else SERIAL_ERROR_MSG("E3MH:1 PRECONDITION");
    return;
  }
  remember_feedrate_scaling_off();
  const float measured_z = probe.material_height(envelope.clearance, envelope.maximum, envelope.minimum);
  restore_feedrate_and_scaling();
  if (isfinite(measured_z)) {
    if (surface) SERIAL_ECHOLNPAIR_F("E3MH:2 Z:", measured_z, 3);
    else SERIAL_ECHOLNPAIR_F("E3MH:1 Z:", measured_z, 3);
  }
  else {
    // Finish native cleanup before printing diagnostic data or the terminal error.
    // Keep the existing failure line so older hosts still fail-stop identically.
    const auto &t = e3_material::trace;
    SERIAL_ECHOPAIR("E3PD:1 STAGE:", t.failed_stage ? t.failed_stage : "ENTRY",
                    " REASON:", t.reason ? t.reason : "PRECONDITION");
    // Marlin's SerialBase float printer does not preserve NaN. Missing evidence
    // must remain explicit text, never appear to be an accepted Z0 contact.
    if (isfinite(t.failed_z)) SERIAL_ECHOPAIR_F(" Z:", t.failed_z, 3);
    else SERIAL_ECHOPGM(" Z:nan");
    if (isfinite(t.fast_z)) SERIAL_ECHOPAIR_F(" FAST:", t.fast_z, 3);
    else SERIAL_ECHOPGM(" FAST:nan");
    if (isfinite(t.slow_z)) SERIAL_ECHOPAIR_F(" SLOW:", t.slow_z, 3);
    else SERIAL_ECHOPGM(" SLOW:nan");
    SERIAL_ECHOPAIR(" CLEANUP_FAILED:", int(t.stow_failed));
    SERIAL_EOL();
    if (surface) SERIAL_ERROR_MSG("E3MH:2 PROBE_FAILED");
    else SERIAL_ERROR_MSG("E3MH:1 PROBE_FAILED");
  }
}
