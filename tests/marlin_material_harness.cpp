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
struct Position { float x, y, z; };
Position current_position;
constexpr int Z_AXIS = 2;
float workspace_z;
bool trusted, running, relative, deploy_fail, stow_fail;
int deploys, stows, touches, lifts, remembers, restores;
float trigger[8], low_seen[8], speeds[8], lifts_seen[8];
bool no_trigger[8];
const char *error;
float result;
struct { bool z_probe_enabled; } endstops;
struct { bool leveling_active; } planner;
struct { const char *command_ptr; float linear_unit_factor; } parser;
bool axis_is_trusted(int) { return trusted; }
bool axis_is_relative(int) { return relative; }
bool IsRunning() { return running; }
extern "C" int strcmp(const char *a, const char *b) {
  while (*a && *a == *b) { ++a; ++b; }
  return (unsigned char)*a - (unsigned char)*b;
}
void remember_feedrate_scaling_off() { ++remembers; }
void restore_feedrate_and_scaling() { ++restores; }
#define SERIAL_ERROR_MSG(X) error = (X)
#define SERIAL_ECHOLNPAIR_F(LABEL,VALUE,PRECISION) result = (VALUE)
void do_blocking_move_to_z(float z, float) {
  lifts_seen[lifts++] = z;
  current_position.z = z;
}
struct Probe {
  static Position offset;
  static float run_z_probe(bool sanity_check = true, bool material = false);
  static float material_height();
  static bool deploy() { ++deploys; return deploy_fail; }
  static bool stow() { ++stows; return stow_fail; }
  static bool probe_down_to_z(float low, float speed) {
    const int i = touches++;
    low_seen[i] = low;
    speeds[i] = speed;
    current_position.z = no_trigger[i] ? low : trigger[i];
    return no_trigger[i];
  }
};
Position Probe::offset;
Probe probe;
constexpr float z_probe_fast_mm_s = 8.0f;
struct GcodeSuite { static void G39(); };

// MARKER

void reset() {
  current_position = { 100, 100, 20 };
  Probe::offset = { -40, -40, 0 };
  workspace_z = 0;
  trusted = running = true;
  relative = deploy_fail = stow_fail = false;
  endstops.z_probe_enabled = planner.leveling_active = false;
  parser.command_ptr = "G39"; parser.linear_unit_factor = 1;
  deploys = stows = touches = lifts = remembers = restores = 0;
  result = NAN; error = nullptr;
  for (int i = 0; i < 8; ++i) { trigger[i] = 5.5f; no_trigger[i] = false; }
}

extern "C" int test_main() {
  reset();
  GcodeSuite::G39();
  CHECK(!error && fabsf(result - 5.5f) < .001f);
  CHECK(touches == 2 && deploys == 1 && stows == 1);
  CHECK(lifts == 1 && lifts_seen[0] == 10.5f);
  CHECK(speeds[0] == 8 && speeds[1] == 4);
  CHECK(low_seen[0] == -2 && low_seen[1] == -2);
  CHECK(remembers == 1 && restores == 1);
  CHECK(current_position.x == 100 && current_position.y == 100 && workspace_z == 0);

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
  return 0;
}
