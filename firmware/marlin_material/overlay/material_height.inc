// SPDX-License-Identifier: GPL-3.0-or-later
// Compiled inside probe.cpp. Native pin control and descent remain in Probe.
namespace e3_material {
  Envelope active_envelope = { start_z, maximum, minimum };
  bool envelope_active = false;
  ProbeTrace trace = { "ENTRY", nullptr, nullptr, NAN, NAN, NAN, false };

  void record_failure(const char *reason, const float z) {
    if (envelope_active && !trace.reason) {
      trace.failed_stage = trace.stage;
      trace.reason = reason;
      trace.failed_z = z;
    }
  }

  struct EnvelopeScope {
    const Envelope previous;
    explicit EnvelopeScope(const float clearance, const float upper, const float lower)
      : previous(active_envelope) {
      active_envelope = { clearance, upper, lower };
      envelope_active = true;
    }
    ~EnvelopeScope() {
      active_envelope = previous;
      envelope_active = false;
    }
  };
}

float Probe::material_height(const float clearance, const float upper, const float lower) {
  // A rejected nested call must not erase the active cycle's evidence.
  if (e3_material::envelope_active) return NAN;
  e3_material::trace = { "ENTRY", nullptr, nullptr, NAN, NAN, NAN, false };
  // Repeat the physical envelope check at the entry to probe handling.
  if (e3_material::envelope_active || !axis_is_trusted(Z_AXIS) || endstops.z_probe_enabled
      || !e3_material::start_ok(current_position.z, offset.z,
          LOGICAL_Z_POSITION(0), Z_CLEARANCE_MULTI_PROBE,
          _MAX(Z_CLEARANCE_BETWEEN_PROBES, Z_CLEARANCE_DEPLOY_PROBE), clearance, upper, lower))
    return NAN;
  const e3_material::EnvelopeScope envelope(clearance, upper, lower);
  float measured_z = NAN;
  e3_material::trace.stage = "DEPLOY";
  if (!deploy()) measured_z = run_z_probe(true, true) + offset.z;
  else e3_material::record_failure("DEPLOY_FAILED", current_position.z);
  // Always attempt native stow after entering deployment, including failure.
  // No G30 move_z_after_probing(): it can descend to a fixed bed clearance.
  e3_material::trace.stage = "STOW";
  if (stow()) {
    e3_material::trace.stow_failed = true;
    e3_material::record_failure("STOW_FAILED", current_position.z);
    measured_z = NAN;
  }
  e3_material::trace.stage = "RESULT";
  if (!axis_is_trusted(Z_AXIS)) {
    e3_material::record_failure("Z_UNTRUSTED", current_position.z);
    return NAN;
  }
  if (!isfinite(measured_z) || measured_z < lower || measured_z > upper) {
    e3_material::record_failure("INVALID_RESULT", current_position.z);
    return NAN;
  }
  return measured_z;
}
