// Explicit idle-only F401 maintenance entry. Marlin never erases or writes flash.
#include "../../MarlinCore.h"
#include "../queue.h"
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
  // The dispatcher retains the current command until its handler returns.
  // Require M997 alone, including no queued/injected work behind it.
  if (strcmp(parser.command_ptr, "M997") || !IsRunning()
      || queue.ring_buffer.length != 1 || queue.injected_commands_P || queue.injected_commands[0]
      || planner.has_blocks_queued() || endstops.z_probe_enabled
      #if HAS_HOTEND
        || thermalManager.degTargetHotend(0) != 0
      #endif
      #if HAS_HEATED_BED
        || thermalManager.degTargetBed() != 0
      #endif
      || thermalManager.fan_speed[0] || thermalManager.fan_speed[1]
      #if ENABLED(SDSUPPORT)
        || card.isPrinting() || card.isFileOpen() || card.flag.saving
      #endif
  ) {
    SERIAL_ERROR_MSG("E3USB:1 PRECONDITION");
    return;
  }
  const uint32_t device = DBGMCU->IDCODE & 0xFFFU;
  const uint16_t flash_kib = *(volatile uint16_t *)0x1FFF7A22;
  const uint32_t stack = *(volatile uint32_t *)0x08010000;
  const uint32_t entry = *(volatile uint32_t *)0x08010004;
  // Both supported capacities use the same conservative 64 KiB RAM image.
  // The updater has 101 vector words (404 bytes); reset must follow them.
  if (!((device == 0x423U && flash_kib == 256U) || (device == 0x433U && flash_kib == 512U))
      || stack <= 0x20000000 || stack > 0x20010000 || (stack & 7U)
      || !(entry & 1U) || (entry & ~1U) < 0x08010194 || (entry & ~1U) >= 0x08020000) {
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
  SCB->VTOR = 0x08010000;
  __DSB();
  __ISB();
  e3_enter_updater(stack, entry);
}
