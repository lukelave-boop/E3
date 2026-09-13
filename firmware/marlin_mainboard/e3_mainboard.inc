// Readback of commanded native outputs. This does not measure connector voltage.
#include "../../module/probe.h"
#include "../../module/endstops.h"
#include "../../module/e3_material_height.h"
#include "../../module/planner.h"
#include "../../MarlinCore.h"
#include "../queue.h"
#if ENABLED(EMERGENCY_PARSER)
  #include "../../feature/e_parser.h"
#endif

static_assert(FAN_COUNT == 2, "E3 requires exactly two independent fan channels");
static_assert(FAN_PIN == PA0 && FAN1_PIN == PC0, "E3 fan pin mapping changed");
static_assert(E0_AUTO_FAN_PIN == -1, "FAN1 must not be temperature controlled");

void GcodeSuite::M123() {
  if (strcmp(parser.command_ptr, "M123")) {
    SERIAL_ERROR_MSG("E3MB:1 ARGUMENTS");
    return;
  }
  SERIAL_ECHOPAIR("E3MB:1 FAN1:", int(thermalManager.fan_speed[1]),
                  " FAN2:", int(thermalManager.fan_speed[0]),
                  " Z_KNOWN:", int(axis_is_trusted(Z_AXIS)));
  SERIAL_EOL();
}

// Only the typed E3 clean-shutdown path may request this coordinate adoption.
// It neither probes nor moves, and cannot prove that an unpowered axis stayed put.
static bool e3_restore_z_argument(const char *text, float &z) {
  if (strncmp(text, "M124", 4)) return false;
  text += 4;
  if (*text != ' ' && *text != '\t') return false;
  while (*text == ' ' || *text == '\t') ++text;
  if (*text++ != 'Z' || !e3_material::decimal(text, z)) return false;
  while (*text == ' ' || *text == '\t') ++text;
  return !*text && z >= 0.0f && z <= e3_material::ceiling;
}

void GcodeSuite::M124() {
  float z;
  if (!e3_restore_z_argument(parser.command_ptr, z)) {
    SERIAL_ERROR_MSG("E3ZR:1 ARGUMENTS");
    return;
  }
  if (!IsRunning() || LOGICAL_Z_POSITION(0) != 0.0f
      || axis_is_relative(Z_AXIS) || endstops.z_probe_enabled
      || e3_material::envelope_active || thermalManager.fan_speed[0]
      || planner.has_blocks_queued() || planner.cleaning_buffer_counter
      || queue.ring_buffer.length > 1 || queue.injected_commands_P
      || queue.injected_commands[0]
      #if HAS_LEVELING
        || planner.leveling_active
      #endif
      #if ENABLED(INCH_MODE_SUPPORT)
        || parser.linear_unit_factor != 1.0f
      #endif
      #if ENABLED(EMERGENCY_PARSER)
        || emergency_parser.killed_by_M112 || emergency_parser.quickstop_by_M410
      #endif
  ) {
    SERIAL_ERROR_MSG("E3ZR:1 PRECONDITION");
    return;
  }
  if (!axis_homed && !axis_trusted && current_position.z == 0.0f) {
    // Reset Z0 alone is never evidence of physical position. The consumed host
    // checkpoint is the authority for this one assignment after a cold start.
    current_position.z = z;
    sync_plan_position();  // Empty planner: updates its and the stepper's counters.
    set_axis_homed(Z_AXIS);
    set_axis_trusted(Z_AXIS);
  }
  else {
    // Reconnecting to still-powered firmware only verifies existing authority.
    // A formerly-homed but disabled axis cannot qualify, and a mismatch never
    // adjusts counters. Compare all coordinates at the protocol's mm precision.
    const float executed_z = planner.get_axis_position_mm(Z_AXIS);
    if (!axis_was_homed(Z_AXIS) || !axis_is_trusted(Z_AXIS)
        || !isfinite(current_position.z) || !isfinite(executed_z)
        || roundf(current_position.z * 1000.0f) != roundf(z * 1000.0f)
        || roundf(LOGICAL_Z_POSITION(current_position.z) * 1000.0f) != roundf(z * 1000.0f)
        || roundf(executed_z * 1000.0f) != roundf(z * 1000.0f)) {
      SERIAL_ERROR_MSG("E3ZR:1 PRECONDITION");
      return;
    }
  }
  SERIAL_ECHOLNPAIR_F("E3ZR:1 Z:", current_position.z, 3);
}
