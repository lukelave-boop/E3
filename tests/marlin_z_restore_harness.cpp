// SPDX-License-Identifier: GPL-3.0-or-later
// Executes the actual M124 overlay with counter-only planner and fake I/O.
#include <math.h>
#include <stdint.h>
#include "e3_material_height.h"
#define ENABLED(X) X
#define EMERGENCY_PARSER 1
#define HAS_LEVELING 1
#define INCH_MODE_SUPPORT 1
#define FAN_COUNT 2
#define PA0 0
#define PC0 1
#define FAN_PIN PA0
#define FAN1_PIN PC0
#define E0_AUTO_FAN_PIN -1
#define LOGICAL_Z_POSITION(Z) ((Z) + workspace_z)
#define CHECK(X) do { if (!(X)) return __LINE__; } while (0)

extern "C" int strncmp(const char *a, const char *b, unsigned n) {
  while (n--) {
    const unsigned char av = *a++, bv = *b++;
    if (av != bv) return av - bv;
    if (!av) return 0;
  }
  return 0;
}
extern "C" int strcmp(const char *a, const char *b) {
  while (*a && *a == *b) { ++a; ++b; }
  return (unsigned char)*a - (unsigned char)*b;
}
extern "C" void *memcpy(void *destination, const void *source, unsigned count) {
  auto *out = static_cast<unsigned char *>(destination);
  const auto *in = static_cast<const unsigned char *>(source);
  while (count--) *out++ = *in++;
  return destination;
}

constexpr int Z_AXIS = 2;
int axis_homed, axis_trusted;
float workspace_z;
bool running, relative;
struct Position { float x, y, z, e; } current_position, executed_position;
int synchronizations;
bool IsRunning() { return running; }
bool axis_is_relative(int) { return relative; }
bool axis_was_homed(int axis) { return axis_homed & (1 << axis); }
bool axis_is_trusted(int axis) { return axis_trusted & (1 << axis); }
void set_axis_homed(int axis) { axis_homed |= 1 << axis; }
void set_axis_trusted(int axis) { axis_trusted |= 1 << axis; }
void sync_plan_position() {
  ++synchronizations;
  executed_position = current_position;
}
struct { const char *command_ptr; float linear_unit_factor; } parser;
struct { bool z_probe_enabled; } endstops;
namespace e3_material { bool envelope_active; }
struct { int fan_speed[2]; } thermalManager;
struct {
  bool blocks, leveling_active;
  int cleaning_buffer_counter;
  bool has_blocks_queued() { return blocks; }
  float get_axis_position_mm(int) { return executed_position.z; }
} planner;
struct {
  struct { int length; } ring_buffer;
  const char *injected_commands_P;
  char injected_commands[2];
} queue;
struct { bool killed_by_M112, quickstop_by_M410; } emergency_parser;
const char *error, *result_label;
float result_z;
#define SERIAL_ERROR_MSG(X) error = (X)
#define SERIAL_ECHOPAIR(...) ((void)0)
#define SERIAL_EOL() ((void)0)
#define SERIAL_ECHOLNPAIR_F(LABEL, VALUE, PRECISION) do { result_label = (LABEL); result_z = (VALUE); } while (0)
struct GcodeSuite { static void M123(); static void M124(); };

// MARKER

void reset(const char *command) {
  axis_homed = axis_trusted = synchronizations = 0;
  current_position.x = executed_position.x = 11;
  current_position.y = executed_position.y = 12;
  current_position.z = executed_position.z = 0;
  current_position.e = executed_position.e = 13;
  running = true;
  relative = false;
  workspace_z = 0;
  parser.command_ptr = command;
  parser.linear_unit_factor = 1;
  endstops.z_probe_enabled = e3_material::envelope_active = false;
  thermalManager.fan_speed[0] = thermalManager.fan_speed[1] = 0;
  planner.blocks = planner.leveling_active = false;
  planner.cleaning_buffer_counter = 0;
  queue.ring_buffer.length = 1;
  queue.injected_commands_P = nullptr;
  queue.injected_commands[0] = 0;
  emergency_parser.killed_by_M112 = emergency_parser.quickstop_by_M410 = false;
  error = result_label = nullptr;
  result_z = NAN;
}

extern "C" int test_main() {
  const char *invalid[] = {
    "", "M124", "M124 ", "M124 Z", "M124Z20", "M124.1 Z20", "M124 Z20 Z21",
    "M124 X20", "M124 Z20 X1", "M124 Z20;anything", "M124 Z20G1", "M124 Z20\n",
    "M124 Znan", "M124 Zinf", "M124 Z1e1", "M124 Z-0.001", "M124 Z80.001",
    "M124 Z+", "M124 Z.", "M124 Z20.1.1", "M124 Z 20", "M124 z20",
    "M124 Z9999999999999999999999999999999999999999999999999999999"
  };
  for (unsigned i = 0; i < sizeof(invalid) / sizeof(invalid[0]); ++i) {
    reset(invalid[i]);
    GcodeSuite::M124();
    CHECK(error && !strcmp(error, "E3ZR:1 ARGUMENTS"));
    CHECK(!result_label && !synchronizations && !axis_homed && !axis_trusted);
    CHECK(current_position.z == 0 && executed_position.z == 0);
  }
  for (unsigned i = 0; i < 19; ++i) {
    reset("M124 Z30.000");
    switch (i) {
      case 0: running = false; break;
      case 1: axis_homed = 4; break;
      case 2: axis_trusted = 4; break;
      case 3: axis_homed = 1; break;
      case 4: axis_trusted = 2; break;
      case 5: current_position.z = 0.001f; break;
      case 6: current_position.z = NAN; break;
      case 7: workspace_z = 1; break;
      case 8: relative = true; break;
      case 9: endstops.z_probe_enabled = true; break;
      case 10: e3_material::envelope_active = true; break;
      case 11: thermalManager.fan_speed[0] = 1; break;
      case 12: planner.blocks = true; break;
      case 13: planner.cleaning_buffer_counter = 1; break;
      case 14: queue.ring_buffer.length = 2; break;
      case 15: queue.injected_commands_P = "G1 Z0"; break;
      case 16: queue.injected_commands[0] = 'G'; break;
      case 17: planner.leveling_active = true; break;
      case 18: parser.linear_unit_factor = 25.4f; break;
    }
    const int homed = axis_homed, trusted = axis_trusted;
    GcodeSuite::M124();
    CHECK(error && !strcmp(error, "E3ZR:1 PRECONDITION"));
    CHECK(!result_label && !synchronizations && executed_position.z == 0);
    CHECK(axis_homed == homed && axis_trusted == trusted);
  }
  for (int i = 0; i < 2; ++i) {
    reset("M124 Z30");
    if (i) emergency_parser.killed_by_M112 = true;
    else emergency_parser.quickstop_by_M410 = true;
    GcodeSuite::M124();
    CHECK(error && !synchronizations && !axis_trusted && !axis_homed);
  }
  const char *valid[] = {"M124 Z0", "M124 Z30.125", "M124\tZ+80.000\t"};
  const float values[] = {0, 30.125f, 80};
  for (unsigned i = 0; i < sizeof(valid) / sizeof(valid[0]); ++i) {
    reset(valid[i]);
    thermalManager.fan_speed[1] = 255;  // Independent CPU cooling is unchanged.
    GcodeSuite::M124();
    CHECK(!error && result_label && !strcmp(result_label, "E3ZR:1 Z:"));
    CHECK(result_z == values[i] && current_position.z == values[i]);
    CHECK(executed_position.z == values[i] && synchronizations == 1);
    CHECK(axis_homed == 4 && axis_trusted == 4);
    CHECK(current_position.x == 11 && current_position.y == 12 && current_position.e == 13);
    CHECK(executed_position.x == 11 && executed_position.y == 12 && executed_position.e == 13);
    CHECK(!thermalManager.fan_speed[0] && thermalManager.fan_speed[1] == 255);
    CHECK(!endstops.z_probe_enabled && !planner.blocks);
    // A duplicate becomes a read-only verification, with no counter reassignment.
    GcodeSuite::M124();
    CHECK(!error && synchronizations == 1 && axis_homed == 4 && axis_trusted == 4);
  }
  for (int i = 0; i < 8; ++i) {
    reset("M124 Z30.000");
    current_position.z = executed_position.z = 30;
    axis_homed = axis_trusted = 4;
    switch (i) {
      case 0: current_position.z = 30.002f; break;
      case 1: executed_position.z = 30.002f; break;
      case 2: current_position.z = NAN; break;
      case 3: executed_position.z = NAN; break;
      case 4: workspace_z = 1; break;
      case 5: planner.leveling_active = true; break;
      case 6: axis_homed = 0; break;
      case 7: axis_trusted = 0; break;
    }
    const int homed = axis_homed, trusted = axis_trusted;
    GcodeSuite::M124();
    CHECK(error && !strcmp(error, "E3ZR:1 PRECONDITION"));
    CHECK(!result_label && !synchronizations && axis_homed == homed && axis_trusted == trusted);
  }
  reset("M124 Z30.000");
  current_position.z = 30.0001f;
  executed_position.z = 29.9999f;
  axis_homed = axis_trusted = 4;
  GcodeSuite::M124();
  CHECK(!error && !synchronizations && result_label && axis_homed == 4 && axis_trusted == 4);
  CHECK(current_position.z == 30.0001f && executed_position.z == 29.9999f);
  // Z_SAFE_HOMING can establish the disconnected virtual XY axes along with Z.
  // An ordinary powered reconnect preserves those flags; it never clears them.
  reset("M124 Z30.000");
  current_position.z = executed_position.z = 30;
  axis_homed = axis_trusted = 7;
  GcodeSuite::M124();
  CHECK(!error && !synchronizations && result_label && axis_homed == 7 && axis_trusted == 7);
  CHECK(current_position.x == 11 && current_position.y == 12 && current_position.e == 13);
  CHECK(executed_position.x == 11 && executed_position.y == 12 && executed_position.e == 13);
  CHECK(current_position.z == 30 && executed_position.z == 30);
  return 0;
}
