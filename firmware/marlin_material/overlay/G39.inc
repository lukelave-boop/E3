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
  e3_material::Envelope envelope = { e3_material::start_z, e3_material::maximum };
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
          envelope.clearance, envelope.maximum)
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
  const float measured_z = probe.material_height(envelope.clearance, envelope.maximum);
  restore_feedrate_and_scaling();
  if (isfinite(measured_z)) {
    if (surface) SERIAL_ECHOLNPAIR_F("E3MH:2 Z:", measured_z, 3);
    else SERIAL_ECHOLNPAIR_F("E3MH:1 Z:", measured_z, 3);
  }
  else {
    if (surface) SERIAL_ERROR_MSG("E3MH:2 PROBE_FAILED");
    else SERIAL_ERROR_MSG("E3MH:1 PROBE_FAILED");
  }
}
