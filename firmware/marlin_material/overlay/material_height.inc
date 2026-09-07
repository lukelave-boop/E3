// SPDX-License-Identifier: GPL-3.0-or-later
// Compiled inside probe.cpp. Native pin control and descent remain in Probe.
float Probe::material_height() {
  // Repeat the physical envelope check at the entry to probe handling.
  if (!axis_is_trusted(Z_AXIS) || endstops.z_probe_enabled
      || !e3_material::start_ok(current_position.z, offset.z,
          LOGICAL_Z_POSITION(0), Z_CLEARANCE_MULTI_PROBE,
          _MAX(Z_CLEARANCE_BETWEEN_PROBES, Z_CLEARANCE_DEPLOY_PROBE)))
    return NAN;
  float measured_z = NAN;
  if (!deploy()) measured_z = run_z_probe(true, true) + offset.z;
  // Always attempt native stow after entering deployment, including failure.
  // No G30 move_z_after_probing(): it can descend to a fixed bed clearance.
  if (stow()) measured_z = NAN;
  if (!axis_is_trusted(Z_AXIS) || !isfinite(measured_z)
      || measured_z < e3_material::minimum || measured_z > e3_material::maximum)
    return NAN;
  return measured_z;
}
