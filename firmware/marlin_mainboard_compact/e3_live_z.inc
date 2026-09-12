// SPDX-License-Identifier: GPL-3.0-or-later
#include "e3_live_z.h"

e3_live_z::Reporter e3_live_z::reporter;

void GcodeSuite::M154() {
  if (!e3_live_z::reporter.configure(parser.command_ptr, millis()))
    SERIAL_ERROR_MSG("E3Z:1 ARGUMENTS");
}

void e3_live_z::tick() {
  if (!reporter.due(millis())) return;

  // Read executed step counters. Never use current_position/planner targets.
  // This profile is Cartesian; use a local snapshot rather than global cartes.
  xyze_pos_t actual = {
    planner.get_axis_position_mm(X_AXIS), planner.get_axis_position_mm(Y_AXIS),
    planner.get_axis_position_mm(Z_AXIS), 0
  };
  TERN_(HAS_POSITION_MODIFIERS, planner.unapply_modifiers(actual, true));
  const float z = LOGICAL_Z_POSITION(actual.z);
  if (!isfinite(z) || z < -9999999.0f || z > 9999999.0f) return;
  const bool homing = reporter.homing_depth != 0,
             known = !homing && axis_is_trusted(Z_AXIS),
             moving = planner.has_blocks_queued();
  // One initial/final sample, then silence at an unchanged idle position. This
  // also bounds serial traffic when a host disappears before disabling reports.
  if (!reporter.changed(z, known, homing, moving)) return;
  char z_text[20], line[64];
  dtostrf(z, 1, 3, z_text);
  const int length = snprintf(line, sizeof(line),
    "E3Z:1 N:%lu Z:%s K:%u H:%u M:%u\n",
    (unsigned long)reporter.sequence, z_text,
    unsigned(known), unsigned(homing), unsigned(moving));
  if (length <= 0 || unsigned(length) >= sizeof(line)) return;

  // The sole USART1 writer runs in the main loop, never the stepper/RX ISR.
  // Fit the complete line in the TX ring so this hook cannot block motion idle.
  if (MSerial1.availableForWrite() < length) return;
  MSerial1.write((const uint8_t *)line, length);
  reporter.sent(z, known, homing, moving);
}
