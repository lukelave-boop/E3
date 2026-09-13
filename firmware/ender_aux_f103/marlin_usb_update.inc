// Explicit idle-only maintenance entry. No flash is written by Marlin.
#include "../../MarlinCore.h"
#include "../../module/planner.h"
#include "../../module/temperature.h"
#include "../../module/endstops.h"
#include "../../sd/cardreader.h"

__attribute__((naked, noreturn, noinline))
static void e3_enter_updater(uint32_t stack __attribute__((unused)), uint32_t entry __attribute__((unused))) {
  __asm volatile(
    "cpsid i\n"
    "msr msp, r0\n"
    "movs r2, #0\n"
    "msr control, r2\n"
    "msr basepri, r2\n"
    "msr faultmask, r2\n"
    "dsb\n"
    "isb\n"
    "bx r1\n");
}

void GcodeSuite::M997() {
  if (strcmp(parser.command_ptr, "M997") || !IsRunning()
      || planner.has_blocks_queued() || endstops.z_probe_enabled
      || thermalManager.degTargetHotend(0) != 0 || thermalManager.degTargetBed() != 0
      || thermalManager.fan_speed[0] || thermalManager.fan_speed[1]
      #if ENABLED(SDSUPPORT)
        || card.isPrinting()
      #endif
  ) {
    SERIAL_ERROR_MSG("E3USB:1 PRECONDITION");
    return;
  }
  const uint32_t stack = *(volatile uint32_t *)0x08007000;
  const uint32_t entry = *(volatile uint32_t *)0x08007004;
  if ((DBGMCU->IDCODE & 0xFFFU) != 0x414U || *(volatile uint16_t *)0x1FFFF7E0 != 512U
      || stack <= 0x20000000 || stack > 0x20010000 || (stack & 7U)
      || !(entry & 1U) || (entry & ~1U) < 0x08007130 || (entry & ~1U) >= 0x08010000) {
    SERIAL_ERROR_MSG("E3USB:1 UPDATER_INVALID");
    return;
  }
  thermalManager.disable_all_heaters();
  thermalManager.zero_fan_speeds();
  WRITE(FAN_PIN, LOW);
  WRITE(FAN1_PIN, LOW);
  disable_all_steppers();
  SERIAL_ECHOLNPGM("E3USB:1 ENTERING_UPDATER");
  SERIAL_FLUSHTX();
  __disable_irq();
  SysTick->CTRL = 0;
  SCB->VTOR = 0x08007000;
  __DSB();
  __ISB();
  e3_enter_updater(stack, entry);
}
