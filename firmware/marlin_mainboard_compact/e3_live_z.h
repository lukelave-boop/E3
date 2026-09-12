// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once

#include <stdint.h>
#include <string.h>

// Opt-in observational telemetry. It never authorizes or queues motion.
namespace e3_live_z {
  struct Reporter {
    bool enabled = false;
    uint32_t next_ms = 0, sequence = 0;
    unsigned homing_depth = 0;
    bool published = false, last_known = false, last_homing = false, last_moving = false;
    float last_z = 0;

    bool configure(const char *command, const uint32_t now) {
      if (!strcmp(command, "M154 S0")) enabled = false;
      else if (!strcmp(command, "M154 S1")) {
        enabled = true;
        next_ms = now;
      }
      else return false;
      published = false;
      return true;
    }

    bool due(const uint32_t now) {
      if (!enabled || int32_t(now - next_ms) < 0) return false;
      // A congested UART drops this sample instead of accumulating a backlog.
      next_ms = now + 200;
      return true;
    }

    bool changed(const float z, const bool known, const bool homing, const bool moving) const {
      return !published || moving || homing || z != last_z || known != last_known
        || homing != last_homing || moving != last_moving;
    }

    void sent(const float z, const bool known, const bool homing, const bool moving) {
      published = true;
      last_z = z; last_known = known; last_homing = homing; last_moving = moving;
      ++sequence;
    }
  };

  extern Reporter reporter;

  // Native homing resets its stepper origin repeatedly before establishing Z0.
  // Existing axis_trusted alone does not indicate a valid frame during G28.
  struct HomingScope {
    HomingScope() { ++reporter.homing_depth; }
    ~HomingScope() { --reporter.homing_depth; }
  };

  void tick();
}
