// SPDX-License-Identifier: GPL-3.0-or-later
// E3's fixed travel profile, independent of persisted printer speed settings.
#pragma once

namespace e3_z_setup_speed {
  constexpr float travel_max_mm_s = 20.0f;
  constexpr float native_max_mm_s = 5.0f;

  inline void configure(float &maximum) { maximum = travel_max_mm_s; }
  inline bool ready(const float maximum) { return maximum == travel_max_mm_s; }

  // Preserve the old effective planner ceiling through complete native contact
  // cycles, including deploy/retract/stow, nested calls and early returns.
  class NativeCycle {
    float &maximum;
    const float previous;
  public:
    explicit NativeCycle(float &value) : maximum(value), previous(value) {
      if (maximum > native_max_mm_s) maximum = native_max_mm_s;
    }
    ~NativeCycle() { maximum = previous; }
    NativeCycle(const NativeCycle &) = delete;
    NativeCycle &operator=(const NativeCycle &) = delete;
  };
}
