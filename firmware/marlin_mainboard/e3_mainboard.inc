// Readback of commanded native outputs. This does not measure connector voltage.
#include "../../module/probe.h"
#include "../../module/endstops.h"

static_assert(FAN_COUNT == 2, "E3 requires exactly two independent fan channels");
static_assert(FAN_PIN == PA0 && FAN1_PIN == PC0, "E3 fan pin mapping changed");
static_assert(E0_AUTO_FAN_PIN == -1, "FAN1 must not be temperature controlled");

void GcodeSuite::M123() {
  if (strcmp(parser.command_ptr, "M123")) {
    SERIAL_ERROR_MSG("E3MB:1 ARGUMENTS");
    return;
  }
  SERIAL_ECHOPAIR("E3MB:1 FAN1:", int(thermalManager.fan_speed[1]),
                  " FAN2:", int(thermalManager.fan_speed[0]),
                  " Z_KNOWN:", int(axis_is_trusted(Z_AXIS)));
  SERIAL_EOL();
}
