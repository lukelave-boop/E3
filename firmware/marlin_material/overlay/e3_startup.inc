// SPDX-License-Identifier: GPL-3.0-or-later
// Linked with --wrap=SystemInit. The retained updater deliberately leaves
// PRIMASK set. Restore interrupts after CMSIS installs our relocated VTOR,
// before Arduino's constructors start SysTick and use HAL timeouts.
extern "C" void __real_SystemInit();
extern "C" void __wrap_SystemInit() {
  __real_SystemInit();
  __DSB();
  __ISB();
  __enable_irq();
}
