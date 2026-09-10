/* Minimal GNU Arm startup shared by the updater and inert application.
 * No vendor runtime, constructors, heap, floating-point instructions or libc.
 */
#include <stdint.h>

#include "platform.h"

extern uint32_t _estack;
extern uint32_t _sidata;
extern uint32_t _sdata;
extern uint32_t _edata;
extern uint32_t _sbss;
extern uint32_t _ebss;
extern int main(void);
extern void SysTick_Handler(void);
extern void platform_early_startup(void);

void Reset_Handler(void);
__attribute__((noreturn)) static void fault_handler(void);
__attribute__((noreturn, used)) void reset_c(void);

/* STM32F401xE's final external interrupt is SPI4, IRQ 84. Reserved vectors
 * deliberately enter the same output-off handler as unexpected interrupts.
 * The linker enforces 512-byte alignment for all 101 vector entries.
 */
__attribute__((section(".isr_vector"), used))
const uintptr_t vector_table[101] = {
    [0] = (uintptr_t)&_estack,
    [1] = (uintptr_t)Reset_Handler,
    [2 ... 14] = (uintptr_t)fault_handler,
    [15] = (uintptr_t)SysTick_Handler,
    [16 ... 100] = (uintptr_t)fault_handler,
};

__attribute__((naked, noreturn)) void Reset_Handler(void)
{
    /* A first-stage jump is not a hardware reset. Establish MSP/privileged
     * thread state before any C prologue; leave IRQs masked until init ends.
     */
    __asm volatile(
        "cpsid i\n"
        "ldr r0, =_estack\n"
        "msr msp, r0\n"
        "movs r0, #0\n"
        "msr control, r0\n"
        "msr basepri, r0\n"
        "msr faultmask, r0\n"
        "isb\n"
        "b reset_c\n");
}

__attribute__((noreturn, used)) void reset_c(void)
{
    /* Stop inherited DMA, establish vectors and GPIO states before copying
     * RAM code or clearing application state. No global state is read here.
     */
    platform_early_startup();
    platform_service();
    uint32_t exception_number;
    __asm volatile("mrs %0, ipsr" : "=r"(exception_number));
    if (exception_number != 0) {
        /* A branch from inside a retained-loader ISR is not a valid reset
         * handoff. Do not continue with an exception permanently active.
         */
        fault_handler();
    }

    const volatile uint32_t *source = &_sidata;
    for (volatile uint32_t *dest = &_sdata; dest < &_edata; ++dest) {
        *dest = *source++;
        platform_service();
    }
    for (volatile uint32_t *dest = &_sbss; dest < &_ebss; ++dest) {
        *dest = 0;
        platform_service();
    }

    (void)main();
    fault_handler();
}

__attribute__((noreturn)) static void fault_handler(void)
{
    __asm volatile("cpsid i" ::: "memory");
    platform_safe_outputs();
    /* No automatic reboot loop, motion, probe pulse or flash operation after
     * an unexpected exception. Power cycling remains the recovery entry.
     */
    for (;;) {
        platform_service();
        __asm volatile("nop");
    }
}
