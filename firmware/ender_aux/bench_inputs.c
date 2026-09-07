/* The S1-family source configuration maps the probe input to PC14:
 * https://github.com/Klipper3d/klipper/blob/master/config/printer-creality-ender3-s1-2021.cfg
 * Register definitions: ST RM0368 GPIO chapter and STM32F401xE device header:
 * https://github.com/STMicroelectronics/cmsis-device-f4/blob/master/Include/stm32f401xe.h
 *
 * This exposes a raw input for bench observation or an optional dry switch.
 * Board connector/pad mapping remains physically unverified. No connector
 * wiring instruction or CR Touch trigger polarity is established here.
 * PC13 (probe control) and every output are deliberately untouched.
 */
#include "bench_inputs.h"

#define REG32(address) (*(volatile uint32_t *)(uintptr_t)(address))
#define RCC_AHB1ENR REG32(UINT32_C(0x40023830))
#define GPIOC_MODER REG32(UINT32_C(0x40020800))
#define GPIOC_PUPDR REG32(UINT32_C(0x4002080C))
#define GPIOC_IDR REG32(UINT32_C(0x40020810))
#define PC14_FIELD (UINT32_C(3) << 28)

void bench_inputs_init(void)
{
    RCC_AHB1ENR |= UINT32_C(1) << 2;
    (void)RCC_AHB1ENR;
    __asm volatile("dsb" ::: "memory");
    GPIOC_MODER &= ~PC14_FIELD;
    GPIOC_PUPDR = (GPIOC_PUPDR & ~PC14_FIELD) | (UINT32_C(1) << 28);
}

uint32_t bench_probe_raw(void)
{
    return (GPIOC_IDR >> 14) & UINT32_C(1);
}
