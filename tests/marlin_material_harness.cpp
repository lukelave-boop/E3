// SPDX-License-Identifier: GPL-3.0-or-later
// Production Marlin functions are inserted at MARKER by run_native_tests.py.
#include <math.h>
#include <stdint.h>
#include "e3_material_height.h"
#define ENABLED(X) X
#define DEBUG_LEVELING_FEATURE 0
#define FIX_MOUNTED_PROBE 0
#define LOOP_LE_N(I,N) for (uint8_t I = 0; I <= (N); ++I)
#define LOOP_S_LE_N(I,S,N) for (uint8_t I = (S); I <= (N); ++I)
#define ABS(X) fabsf(X)
#define HAS_LEVELING 1
#define INCH_MODE_SUPPORT 1
#define DEBUG_SECTION(...)
#define DEBUGGING(...) false
#define DEBUG_ECHOLNPAIR(...) ((void)0)
#define DEBUG_ECHOLNPGM(...) ((void)0)
#define TERN0(...) 0
#define TERN_(...)
#if E3_NATIVE_PIN_TEST
  #undef TERN0
  #define E3_TERN0_0(EXPRESSION) 0
  #define E3_TERN0_1(EXPRESSION) (EXPRESSION)
  #define E3_TERN0_SELECT(CONDITION,EXPRESSION) E3_TERN0_##CONDITION(EXPRESSION)
  #define TERN0(CONDITION,EXPRESSION) E3_TERN0_SELECT(CONDITION,EXPRESSION)
  #define PROBE_TARE 0
  #define DISABLED(X) (!(X))
  #define BOTH(A,B) ((A) && (B))
  #define EITHER(A,B) ((A) || (B))
  #define BLTOUCH 1
  #define FORCE_INLINE inline
  #define DEBUG_POS(...)
  #define TEST(VALUE,BIT) (((VALUE) & (1U << (BIT))) != 0)
#endif
#define UNUSED(X) (void)(X)
#define PSTR(X) X
#define MMM_TO_MMS(X) ((X) / 60.0f)
#define Z_PROBE_FEEDRATE_SLOW 240
#define Z_PROBE_FEEDRATE_FAST 480
#define Z_CLEARANCE_MULTI_PROBE 5.0f
#define Z_CLEARANCE_BETWEEN_PROBES 5.0f
#define Z_CLEARANCE_DEPLOY_PROBE 10.0f
#define Z_PROBE_LOW_POINT -2.0f
#define _MAX(A,B) ((A) > (B) ? (A) : (B))
#define RECIPROCAL(X) (1.0f / (X))
#define LOGICAL_Z_POSITION(X) ((X) + workspace_z)
#define CHECK(X) do { if (!(X)) return __LINE__; } while (0)
using PGM_P = const char *;
using const_float_t = const float;
using feedRate_t = float;
using const_feedRate_t = const float;
struct xy_pos_t { float x, y; };
struct Position {
  float x, y, z;
  operator xy_pos_t() const { return {x, y}; }
};
Position current_position;
constexpr int Z_AXIS = 2;
float workspace_z;
bool trusted, running, relative, deploy_fail, stow_fail;
int deploys, stows, touches, lifts, remembers, restores;
float trigger[8], low_seen[8], speeds[8], lifts_seen[8];
bool no_trigger[8];
const char *error;
const char *result_label;
float result;
int diagnostic_reports;
template <typename... Values> void serial_diagnostic(Values...) {}
#if E3_NATIVE_PIN_TEST
constexpr int Z_MIN_PROBE = 0;
unsigned trigger_bits;
float stepper_z;
int enables, clears, position_reads, syncs, xy_moves;
int deploy_failure_at, stow_failure_at;
bool pin_deployed;
struct {
  bool z_probe_enabled;
  void enable_z_probe(bool value) { ++enables; z_probe_enabled = value; }
  unsigned trigger_state() const { return trigger_bits; }
  void hit_on_purpose() { ++clears; trigger_bits = 0; }
} endstops;
struct {
  bool deploy() {
    ++deploys;
    if (deploy_fail || deploys == deploy_failure_at) {
      running = false;
      error = "BLTOUCH deploy";
      return true;
    }
    pin_deployed = true;
    return false;
  }
  bool stow() {
    ++stows;
    if (stow_fail || stows == stow_failure_at) {
      running = false;
      error = "BLTOUCH stow";
      return true;
    }
    pin_deployed = false;
    return false;
  }
} bltouch;
void do_blocking_move_to(xy_pos_t xy) {
  ++xy_moves;
  current_position.x = xy.x;
  current_position.y = xy.y;
}
void set_current_from_steppers_for_axis(int) { ++position_reads; current_position.z = stepper_z; }
void sync_plan_position() { ++syncs; }
#else
struct { bool z_probe_enabled; } endstops;
#endif
struct { bool leveling_active; } planner;
struct { const char *command_ptr; float linear_unit_factor; } parser;
bool axis_is_trusted(int) { return trusted; }
bool axis_is_relative(int) { return relative; }
bool IsRunning() { return running; }
extern "C" int strcmp(const char *a, const char *b) {
  while (*a && *a == *b) { ++a; ++b; }
  return (unsigned char)*a - (unsigned char)*b;
}
extern "C" void *memcpy(void *destination, const void *source, unsigned length) {
  auto *out = static_cast<unsigned char *>(destination);
  auto *in = static_cast<const unsigned char *>(source);
  for (unsigned index = 0; index < length; ++index) out[index] = in[index];
  return destination;
}
void remember_feedrate_scaling_off() { ++remembers; }
void restore_feedrate_and_scaling() { ++restores; }
#define SERIAL_ERROR_MSG(X) error = (X)
#define SERIAL_ECHOLNPAIR_F(LABEL,VALUE,PRECISION) do { result = (VALUE); result_label = (LABEL); } while (0)
#define SERIAL_ECHOPAIR(...) serial_diagnostic(__VA_ARGS__)
#define SERIAL_ECHOPAIR_F(...) serial_diagnostic(__VA_ARGS__)
#define SERIAL_EOL() (++diagnostic_reports)
void do_blocking_move_to_z(float z, float speed) {
#if E3_NATIVE_PIN_TEST
  if (z < current_position.z) {
    const int index = touches++;
    low_seen[index] = z;
    speeds[index] = speed;
    stepper_z = no_trigger[index] ? z : trigger[index];
    trigger_bits = no_trigger[index] ? 0 : (1U << Z_MIN_PROBE);
    current_position.z = z; // Native descent must read actual stopped steps back.
    return;
  }
  stepper_z = z;
#else
  UNUSED(speed);
#endif
  lifts_seen[lifts++] = z;
  current_position.z = z;
}
struct Probe {
  static Position offset;
  static float run_z_probe(bool sanity_check = true, bool material = false);
  static float material_height(const float clearance=20.0f, const float upper=10.5f);
#if E3_NATIVE_PIN_TEST
  static bool set_deployed(bool deploy);
  static bool deploy() { return set_deployed(true); }
  static bool stow() { return set_deployed(false); }
  static bool probe_down_to_z(float low, float speed);
  static void do_z_raise(float clearance) {
    const float target = clearance - offset.z;
    if (current_position.z < target) do_blocking_move_to_z(target, z_probe_fast_mm_s);
  }
  static constexpr float z_probe_fast_mm_s = 8.0f;
#else
  static bool deploy() { ++deploys; return deploy_fail; }
  static bool stow() { ++stows; return stow_fail; }
  static bool probe_down_to_z(float low, float speed) {
    const int i = touches++;
    low_seen[i] = low;
    speeds[i] = speed;
    current_position.z = no_trigger[i] ? low : trigger[i];
    return no_trigger[i];
  }
#endif
};
Position Probe::offset;
Probe probe;
constexpr float z_probe_fast_mm_s = 8.0f;
struct GcodeSuite { static void G39(); };

// MARKER

// NATIVE_PIN_MARKER

void reset() {
  current_position = { 100, 100, 20 };
  Probe::offset = { -40, -40, 0 };
  workspace_z = 0;
  trusted = running = true;
  relative = deploy_fail = stow_fail = false;
  endstops.z_probe_enabled = planner.leveling_active = false;
  parser.command_ptr = "G39"; parser.linear_unit_factor = 1;
  deploys = stows = touches = lifts = remembers = restores = 0;
  result = NAN; error = result_label = nullptr;
  diagnostic_reports = 0;
#if E3_NATIVE_PIN_TEST
  stepper_z = current_position.z;
  trigger_bits = 0;
  enables = clears = position_reads = syncs = xy_moves = 0;
  deploy_failure_at = stow_failure_at = 0;
  pin_deployed = false;
#endif
  for (int i = 0; i < 8; ++i) { trigger[i] = 5.5f; no_trigger[i] = false; }
}

bool envelope_restored() {
  return !e3_material::envelope_active
      && e3_material::active_envelope.clearance == e3_material::start_z
      && e3_material::active_envelope.maximum == e3_material::maximum;
}

#if E3_NATIVE_PIN_TEST
bool failure_is(const char *stage, const char *reason, const float z) {
  const auto &trace = e3_material::trace;
  return trace.failed_stage && !strcmp(trace.failed_stage, stage)
      && trace.reason && !strcmp(trace.reason, reason)
      && (isnan(z) ? isnan(trace.failed_z) : fabsf(trace.failed_z - z) < .001f);
}
#endif

extern "C" int test_main() {
#if E3_NATIVE_PIN_TEST
  // Execute production deploy, descent/readback and stow, with fake pin/step I/O.
  reset(); GcodeSuite::G39();
  CHECK(!error && fabsf(result - 5.5f) < .001f && touches == 2);
  CHECK(position_reads == 2 && syncs == 2 && clears == 2);
  CHECK(low_seen[0] == -2 && low_seen[1] == -2);
  CHECK(speeds[0] == 8 && speeds[1] == 4);
  CHECK(!pin_deployed && !endstops.z_probe_enabled && enables == 2);
  CHECK(deploys == (BLTOUCH_SLOW_MODE ? 3 : 1));
  CHECK(stows == (BLTOUCH_SLOW_MODE ? 3 : 1));
  CHECK(current_position.x == 100 && current_position.y == 100 && xy_moves == 2);
  CHECK(envelope_restored() && remembers == 1 && restores == 1);
  CHECK(diagnostic_reports == 0 && !e3_material::trace.reason);
  CHECK(e3_material::trace.fast_z == 5.5f && e3_material::trace.slow_z == 5.5f);

  // Native deployment error must not fall through to enabling/descent.
  reset(); deploy_fail = true; GcodeSuite::G39();
  CHECK(error && isnan(result) && touches == 0 && enables == 0);
  CHECK(deploys == 1 && !endstops.z_probe_enabled && position_reads == 0);
  CHECK(envelope_restored() && remembers == 1 && restores == 1);
  CHECK(failure_is("DEPLOY", "DEPLOY_FAILED", 20) && diagnostic_reports == 1);
  CHECK(isnan(e3_material::trace.fast_z) && isnan(e3_material::trace.slow_z));

  // An outer stow error must propagate through the actual set_deployed path.
  reset(); stow_failure_at = BLTOUCH_SLOW_MODE ? 3 : 1; GcodeSuite::G39();
  CHECK(error && isnan(result) && touches == 2 && endstops.z_probe_enabled);
  CHECK(enables == 1 && envelope_restored());
  CHECK(failure_is("STOW", "STOW_FAILED", 10) && diagnostic_reports == 1);
  CHECK(e3_material::trace.stow_failed);

  // Missing first/second trigger still uses stopped-step readback and cleanup.
  for (int index = 0; index < 2; ++index) {
    reset(); no_trigger[index] = true; GcodeSuite::G39();
    CHECK(error && isnan(result) && touches == index + 1);
    CHECK(position_reads == index + 1 && syncs == index + 1 && clears == index + 1);
    CHECK(running && !pin_deployed && !endstops.z_probe_enabled && envelope_restored());
    CHECK(failure_is(index ? "SLOW" : "FAST", "NO_TRIGGER", -2));
    CHECK(diagnostic_reports == 1 && !e3_material::trace.stow_failed);
    CHECK(index ? e3_material::trace.fast_z == 5.5f : isnan(e3_material::trace.fast_z));
    CHECK(isnan(e3_material::trace.slow_z));
  }
  // The first cause survives a later cleanup failure; its Z is the stopped step count.
  reset(); no_trigger[0] = true; stow_failure_at = 1; GcodeSuite::G39();
  CHECK(failure_is("FAST", "NO_TRIGGER", -2) && diagnostic_reports == 1);
  CHECK(e3_material::trace.stow_failed && isnan(result) && envelope_restored());

  // Every native contact is checked against the active V2 interval.
  for (int index = 0; index < 2; ++index) {
    reset(); parser.command_ptr = "G39 C30 H15"; current_position.z = stepper_z = 30;
    trigger[index] = 15.01f; GcodeSuite::G39();
    CHECK(failure_is(index ? "SLOW" : "FAST", "CONTACT_RANGE", 15.01f));
    CHECK(touches == index + 1 && diagnostic_reports == 1 && isnan(result));
    CHECK(!e3_material::trace.stow_failed && envelope_restored());
  }
  // G39 C30 H15 must accept the below-border paper interval on both touches.
  reset(); parser.command_ptr = "G39 C30 H15"; current_position.z = stepper_z = 30;
  trigger[0] = trigger[1] = -1.5f; GcodeSuite::G39();
  CHECK(!error && fabsf(result + 1.5f) < .001f && touches == 2);
  CHECK(low_seen[0] == -2 && low_seen[1] == -2 && envelope_restored());
  // reset() deliberately leaves trace alone: a successful new cycle clears old evidence.
  CHECK(diagnostic_reports == 0 && !e3_material::trace.reason);
  CHECK(!e3_material::trace.failed_stage && isnan(e3_material::trace.failed_z));
  CHECK(e3_material::trace.fast_z == -1.5f && e3_material::trace.slow_z == -1.5f);
  CHECK(!e3_material::trace.stow_failed);

  #if BLTOUCH_SLOW_MODE
    // Inner deployment and stow errors are distinct from missing trigger.
    for (int index = 0; index < 2; ++index) {
      reset(); deploy_failure_at = index + 2; GcodeSuite::G39();
      CHECK(error && isnan(result) && !running && touches == index);
      CHECK(position_reads == index && envelope_restored());
      CHECK(failure_is(index ? "SLOW" : "FAST", "DEPLOY_FAILED", index ? 10.5f : 20));
      CHECK(diagnostic_reports == 1 && isnan(e3_material::trace.slow_z));
      reset(); stow_failure_at = index + 1; GcodeSuite::G39();
      CHECK(error && isnan(result) && !running && touches == index + 1);
      // Existing native function returns before stopped-step readback on stow error.
      CHECK(position_reads == index && envelope_restored());
      CHECK(failure_is(index ? "SLOW" : "FAST", "STOW_FAILED", NAN));
      CHECK(diagnostic_reports == 1 && isnan(e3_material::trace.slow_z));
    }
  #endif

  // A nested/rejected command must not erase an active owner's evidence.
  reset(); no_trigger[1] = true; GcodeSuite::G39();
  const auto prior_trace = e3_material::trace;
  const int prior_touches = touches, prior_deploys = deploys, prior_stows = stows;
  e3_material::envelope_active = true;
  CHECK(isnan(probe.material_height(30, 15)));
  CHECK(e3_material::trace.reason == prior_trace.reason);
  CHECK(e3_material::trace.failed_stage == prior_trace.failed_stage);
  CHECK(e3_material::trace.stage == prior_trace.stage);
  CHECK(e3_material::trace.failed_z == prior_trace.failed_z);
  CHECK(e3_material::trace.fast_z == prior_trace.fast_z && isnan(e3_material::trace.slow_z));
  CHECK(e3_material::trace.stow_failed == prior_trace.stow_failed);
  CHECK(touches == prior_touches && deploys == prior_deploys && stows == prior_stows);
  e3_material::envelope_active = false;
  reset(); relative = true; GcodeSuite::G39();
  CHECK(error && !strcmp(error, "E3MH:1 PRECONDITION") && diagnostic_reports == 0);
  CHECK(touches == 0 && deploys == 0 && stows == 0);
  return 0;
#else
  reset();
  GcodeSuite::G39();
  CHECK(!error && fabsf(result - 5.5f) < .001f);
  CHECK(touches == 2 && deploys == 1 && stows == 1);
  CHECK(lifts == 1 && lifts_seen[0] == 10.5f);
  CHECK(speeds[0] == 8 && speeds[1] == 4);
  CHECK(low_seen[0] == -2 && low_seen[1] == -2);
  CHECK(remembers == 1 && restores == 1);
  CHECK(current_position.x == 100 && current_position.y == 100 && workspace_z == 0);
  CHECK(!strcmp(result_label, "E3MH:1 Z:") && envelope_restored());

  // Border, thin, thick and exact interval endpoints; both touches checked.
  const float accepted[] = {-2, 0, .44f, 5.5f, 10.5f};
  for (float z : accepted) {
    reset(); trigger[0] = trigger[1] = z;
    GcodeSuite::G39();
    CHECK(!error && fabsf(result - z) < .001f && touches == 2);
    CHECK(stows == 1 && lifts_seen[0] <= 20);
  }
  // Probe offset conversion and fast/slow native weighting.
  reset(); Probe::offset.z = -2; trigger[0] = 7.5f; trigger[1] = 7.4f;
  GcodeSuite::G39();
  CHECK(!error && fabsf(result - 5.44f) < .001f);
  CHECK(low_seen[0] == 0 && low_seen[1] == 0);

  const float bad[] = {-2.01f, 10.51f, NAN, INFINITY};
  for (float z : bad) for (int index = 0; index < 2; ++index) {
    reset(); trigger[index] = z;
    GcodeSuite::G39();
    CHECK(error && isnan(result) && touches == index + 1 && stows == 1);
    CHECK(restores == 1 && lifts == index);
  }
  for (int index = 0; index < 2; ++index) {
    reset(); no_trigger[index] = true;
    GcodeSuite::G39();
    CHECK(error && isnan(result) && touches == index + 1 && stows == 1);
    CHECK(lifts == index);
  }
  reset(); deploy_fail = true; GcodeSuite::G39();
  CHECK(error && touches == 0 && stows == 1);
  reset(); stow_fail = true; GcodeSuite::G39();
  CHECK(error && isnan(result));

  // Every precondition rejects before deployment, descent or settings changes.
  for (int i = 0; i < 13; ++i) {
    reset();
    switch (i) {
      case 0: trusted = false; break;
      case 1: relative = true; break;
      case 2: running = false; break;
      case 3: workspace_z = 5; break;
      case 4: current_position.z = 19; break;
      case 5: Probe::offset.z = -5; break; // Not enough retract headroom.
      case 6: Probe::offset.z = NAN; break;
      case 7: endstops.z_probe_enabled = true; break;
      case 8: planner.leveling_active = true; break;
      case 9: parser.linear_unit_factor = 25.4f; break;
      case 10: parser.command_ptr = "G39 X110"; break;
      case 11: parser.command_ptr = "G39.1"; break;
      case 12: current_position.z = NAN; break;
    }
    GcodeSuite::G39();
    CHECK(error && deploys == 0 && stows == 0 && touches == 0 && lifts == 0);
    CHECK(remembers == 0 && restores == 0);
  }

  // The existing native bed path still rejects early contact.
  reset(); CHECK(isnan(probe.run_z_probe(true, false)));
  reset(); trigger[0] = trigger[1] = trigger[2] = trigger[3] = 0;
  CHECK(fabsf(probe.run_z_probe(true, false)) < .001f);
  // Upstream TOTAL_PROBING remains in effect for ordinary probing only.
  CHECK(touches == TOTAL_PROBING + (TOTAL_PROBING > 2));

  // V2 owns a temporary envelope, preserving native feeds/retracts/stow.
  const char *surface_commands[] = {"G39 C20 H5", "G39 C40 H25", "G39 H65 C80"};
  const float clearances[] = {20, 40, 80}, upper[] = {5, 25, 65};
  const float offsets[] = {0.0f, -2.0f, -10.0f};
  for (int index = 0; index < 3; ++index) for (float offset : offsets) {
    reset(); parser.command_ptr = surface_commands[index];
    current_position.z = clearances[index]; Probe::offset.z = offset;
    trigger[0] = upper[index] - offset;
    trigger[1] = trigger[0] - 0.1f;
    GcodeSuite::G39();
    CHECK(!error && !strcmp(result_label, "E3MH:2 Z:"));
    CHECK(fabsf(result - (upper[index] - 0.06f)) < .001f);
    CHECK(deploys == 1 && touches == 2 && stows == 1 && lifts == 1);
    CHECK(speeds[0] == 8 && speeds[1] == 4);
    CHECK(low_seen[0] == -2 - offset && low_seen[1] == -2 - offset);
    CHECK(lifts_seen[0] == upper[index] - offset + 5 && lifts_seen[0] <= clearances[index]);
    CHECK(current_position.x == 100 && current_position.y == 100 && workspace_z == 0);
    CHECK(remembers == 1 && restores == 1 && envelope_restored());
  }
  // V2 minimum and maximum remain inclusive, including signed decimal syntax.
  const char *boundary_commands[] = {"G39 C+20.0 H-2.000", "G39 C80.0 H65.000"};
  const float boundary_z[] = {-2, 65};
  for (int index = 0; index < 2; ++index) {
    reset(); parser.command_ptr = boundary_commands[index];
    current_position.z = index ? 80 : 20;
    trigger[0] = trigger[1] = boundary_z[index]; GcodeSuite::G39();
    CHECK(!error && fabsf(result - boundary_z[index]) < .001f && touches == 2);
    CHECK(!strcmp(result_label, "E3MH:2 Z:") && envelope_restored());
  }
  const char *bad_arguments[] = {
    "G39 C80", "G39 H65", "G39 C80 H65 X1", "G39 C80 C80 H65",
    "G39 C80 H65 H65", "G39 C80 H65junk", "G39 C80 H65.1",
    "G39 C19 H5", "G39 C81 H65", "G39 C80 H-2.1", "G39 CNaN H5",
    "G39 Cinf H5", "G39 C20 Hnan", "G39 C20 Hinf", "G39 C H5",
    "G39 C. H5", "G39 C20 H", "G39 C20H5", "G39.1 C20 H5",
    "G39 c20 H5", "G39 C20 h5", "G39 C2e1 H5", "G39 C20 H5;foo"
  };
  for (const char *command : bad_arguments) {
    reset(); parser.command_ptr = command; GcodeSuite::G39();
    CHECK(error && !strcmp(error, "E3MH:2 ARGUMENTS"));
    CHECK(deploys == 0 && touches == 0 && stows == 0 && remembers == 0);
    CHECK(envelope_restored());
  }
  for (int index = 0; index < 12; ++index) {
    reset(); parser.command_ptr = "G39 C80 H65"; current_position.z = 80;
    switch (index) {
      case 0: current_position.z = 79; break;
      case 1: current_position.z = 80.01f; break;
      case 2: current_position.z = NAN; break;
      case 3: Probe::offset.z = -10.01f; break;
      case 4: Probe::offset.z = .01f; break;
      case 5: Probe::offset.z = NAN; break;
      case 6: workspace_z = 1; break;
      case 7: relative = true; break;
      case 8: trusted = false; break;
      case 9: endstops.z_probe_enabled = true; break;
      case 10: planner.leveling_active = true; break;
      case 11: parser.linear_unit_factor = 25.4f; break;
    }
    GcodeSuite::G39();
    CHECK(error && !strcmp(error, "E3MH:2 PRECONDITION"));
    CHECK(deploys == 0 && touches == 0 && stows == 0 && remembers == 0);
    CHECK(envelope_restored());
  }
  // Valid argument pairs still reject when actual offset/retract headroom is insufficient.
  reset(); parser.command_ptr = "G39 C20 H10"; Probe::offset.z = -10;
  GcodeSuite::G39(); CHECK(error && deploys == 0 && envelope_restored());
  // Both touches, missing contacts and deploy/stow failure restore the envelope.
  for (int failure = 0; failure < 12; ++failure) {
    reset(); parser.command_ptr = "G39 C80 H65"; current_position.z = 80;
    trigger[0] = trigger[1] = 50;
    if (failure < 2) trigger[failure] = 65.01f;
    else if (failure < 4) trigger[failure - 2] = -2.01f;
    else if (failure < 6) no_trigger[failure - 4] = true;
    else if (failure == 6) deploy_fail = true;
    else if (failure == 7) stow_fail = true;
    else if (failure < 10) trigger[failure - 8] = NAN;
    else trigger[failure - 10] = INFINITY;
    GcodeSuite::G39();
    CHECK(error && !strcmp(error, "E3MH:2 PROBE_FAILED") && isnan(result));
    CHECK(stows == 1 && restores == 1 && envelope_restored());
    // A following V1 command must still reject a high first contact.
    reset(); trigger[0] = 20; GcodeSuite::G39();
    CHECK(error && touches == 1 && envelope_restored());
  }
  // No nested command may replace an active owner's envelope.
  reset(); e3_material::envelope_active = true;
  parser.command_ptr = "G39 C80 H65"; current_position.z = 80;
  GcodeSuite::G39();
  CHECK(error && deploys == 0 && remembers == 0 && e3_material::envelope_active);
  e3_material::envelope_active = false;
  return 0;
#endif
}
