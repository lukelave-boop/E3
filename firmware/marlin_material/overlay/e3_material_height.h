// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include <math.h>

// V1 is a fixed, native-coordinate contract, independent of retract settings.
// Values are contact heights (nozzle Z + probe offset), not sheet thickness.
namespace e3_material {
  // +10.5 mm above the border is a 12 mm sheet on the recorded -1.5 mm support.
  // Leaves room for the native 5 mm retract with a probe offset down to -4.5 mm.
  constexpr float minimum = -2.0f, maximum = 10.5f, start_z = 20.0f;

  inline bool contact_ok(const float nozzle_z, const float offset_z) {
    const float contact = nozzle_z + offset_z;
    return isfinite(contact) && contact >= minimum && contact <= maximum;
  }

  inline bool start_ok(const float nozzle_z, const float offset_z,
                       const float workspace_z, const float retract,
                       const float deploy_clearance) {
    return isfinite(nozzle_z) && isfinite(offset_z) && isfinite(workspace_z)
        && isfinite(retract) && isfinite(deploy_clearance)
        && fabsf(nozzle_z - start_z) <= 0.05f && workspace_z == 0.0f
        && offset_z <= 0.0f && offset_z >= -10.0f
        && retract > 0.0f && deploy_clearance > 0.0f
        && maximum - offset_z + retract <= nozzle_z
        && deploy_clearance - offset_z <= nozzle_z;
  }
}
