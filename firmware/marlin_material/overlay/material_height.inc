// SPDX-License-Identifier: GPL-3.0-or-later
// Compiled inside probe.cpp. Native pin control and descent remain in Probe.
namespace e3_material {
  Envelope active_envelope = { start_z, maximum };
  bool envelope_active = false;

  struct EnvelopeScope {
    const Envelope previous;
    explicit EnvelopeScope(const float clearance, const float upper)
      : previous(active_envelope) {
      active_envelope = { clearance, upper };
      envelope_active = true;
    }
    ~EnvelopeScope() {
      active_envelope = previous;
      envelope_active = false;
    }
  };
}

float Probe::material_height(const float clearance, const float upper) {
  // Repeat the physical envelope check at the entry to probe handling.
  if (e3_material::envelope_active || !axis_is_trusted(Z_AXIS) || endstops.z_probe_enabled
      || !e3_material::start_ok(current_position.z, offset.z,
          LOGICAL_Z_POSITION(0), Z_CLEARANCE_MULTI_PROBE,
          _MAX(Z_CLEARANCE_BETWEEN_PROBES, Z_CLEARANCE_DEPLOY_PROBE), clearance, upper))
    return NAN;
  const e3_material::EnvelopeScope envelope(clearance, upper);
  float measured_z = NAN;
  if (!deploy()) measured_z = run_z_probe(true, true) + offset.z;
  // Always attempt native stow after entering deployment, including failure.
  // No G30 move_z_after_probing(): it can descend to a fixed bed clearance.
  if (stow()) measured_z = NAN;
  if (!axis_is_trusted(Z_AXIS) || !isfinite(measured_z)
      || measured_z < e3_material::minimum || measured_z > upper)
    return NAN;
  return measured_z;
}
