// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include <math.h>

// V1 is a fixed, native-coordinate contract, independent of retract settings.
// Values are contact heights (nozzle Z + probe offset), not sheet thickness.
namespace e3_material {
  // +10.5 mm above the border is a 12 mm sheet on the recorded -1.5 mm support.
  // Leaves room for the native 5 mm retract with a probe offset down to -4.5 mm.
  constexpr float minimum = -2.0f, maximum = 10.5f, start_z = 20.0f;
  constexpr float surface_minimum = -10.0f, surface_maximum = 65.0f, ceiling = 80.0f;

  struct Envelope { float clearance, maximum, minimum; };
  extern Envelope active_envelope;
  extern bool envelope_active;

  // Diagnostic state for one material cycle only. Never grants motion authority.
  struct ProbeTrace {
    const char *stage, *failed_stage, *reason;
    float failed_z, fast_z, slow_z;
    bool stow_failed;
  };
  extern ProbeTrace trace;
  void record_failure(const char *reason, const float z);

  // Decimal-only syntax is deliberate: no parser truncation, NaN, exponent,
  // unknown/repeated parameters, or trailing non-whitespace text is accepted.
  inline bool decimal(const char *&text, float &value) {
    bool negative = false, digits = false;
    if (*text == '+' || *text == '-') negative = *text++ == '-';
    value = 0;
    while (*text >= '0' && *text <= '9') {
      digits = true;
      value = value * 10 + (*text++ - '0');
    }
    if (*text == '.') {
      ++text;
      float place = 0.1f;
      while (*text >= '0' && *text <= '9') {
        digits = true;
        value += (*text++ - '0') * place;
        place *= 0.1f;
      }
    }
    if (negative) value = -value;
    return digits && isfinite(value);
  }

  inline bool surface_arguments(const char *text, Envelope &envelope) {
    envelope.minimum = surface_minimum;
    if (*text++ != 'G' || *text++ != '3' || *text++ != '9'
        || (*text != ' ' && *text != '\t')) return false;
    bool clearance_seen = false, maximum_seen = false;
    while (*text) {
      while (*text == ' ' || *text == '\t') ++text;
      if (!*text) break;
      const char parameter = *text++;
      float value;
      if (!decimal(text, value) || (*text && *text != ' ' && *text != '\t')) return false;
      if (parameter == 'C' && !clearance_seen) {
        envelope.clearance = value;
        clearance_seen = true;
      }
      else if (parameter == 'H' && !maximum_seen) {
        envelope.maximum = value;
        maximum_seen = true;
      }
      else return false;
    }
    return clearance_seen && maximum_seen
        && envelope.clearance >= start_z && envelope.clearance <= ceiling
        && envelope.maximum >= envelope.minimum && envelope.maximum <= surface_maximum;
  }

  inline bool contact_ok(const float nozzle_z, const float offset_z,
                         const float upper = maximum, const float lower = minimum) {
    const float contact = nozzle_z + offset_z;
    return isfinite(contact) && contact >= lower && contact <= upper;
  }

  inline bool start_ok(const float nozzle_z, const float offset_z,
                       const float workspace_z, const float retract,
                       const float deploy_clearance, const float clearance = start_z,
                       const float upper = maximum, const float lower = minimum) {
    return isfinite(nozzle_z) && isfinite(offset_z) && isfinite(workspace_z)
        && isfinite(retract) && isfinite(deploy_clearance)
        && isfinite(clearance) && clearance >= start_z && clearance <= ceiling
        && isfinite(lower) && lower >= surface_minimum && lower <= upper
        && isfinite(upper) && upper <= surface_maximum
        && fabsf(nozzle_z - clearance) <= 0.05f && nozzle_z <= ceiling
        && workspace_z == 0.0f
        && offset_z <= 0.0f && offset_z >= -10.0f
        && retract > 0.0f && deploy_clearance > 0.0f
        && upper - offset_z + retract <= nozzle_z
        && deploy_clearance - offset_z <= nozzle_z;
  }
}
