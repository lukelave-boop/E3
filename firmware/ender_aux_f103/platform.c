/* STM32F103RET6 communications and retained updater platform.
 * Register facts: ST RM0008 and cmsis-device-f1 stm32f103xe.h.
 * Flash programming: ST PM0075 (2 KiB pages, 16-bit programming).
 * Source-derived S1 pin mapping, not physically qualified.
 * Does not write the factory loader, updater, option bytes, or mass erase.
 */
#include "platform.h"

#include <stddef.h>

#define REG32(address) (*(volatile uint32_t *)(uintptr_t)(address))
#define REG16(address) (*(volatile uint16_t *)(uintptr_t)(address))
#define REG8(address) (*(volatile uint8_t *)(uintptr_t)(address))
#define BIT(number) (UINT32_C(1) << (number))

#define RCC_BASE UINT32_C(0x40021000)
#define RCC_CR REG32(RCC_BASE + 0x00)
#define RCC_CFGR REG32(RCC_BASE + 0x04)
#define RCC_CIR REG32(RCC_BASE + 0x08)
#define RCC_APB2RSTR REG32(RCC_BASE + 0x0C)
#define RCC_APB1RSTR REG32(RCC_BASE + 0x10)
#define RCC_AHBENR REG32(RCC_BASE + 0x14)
#define RCC_APB2ENR REG32(RCC_BASE + 0x18)
#define RCC_APB1ENR REG32(RCC_BASE + 0x1C)
#define GPIO_A UINT32_C(0x40010800)
#define GPIO_B UINT32_C(0x40010C00)
#define GPIO_C UINT32_C(0x40011000)
#define GPIO_CRL(port) REG32((port) + 0x00)
#define GPIO_CRH(port) REG32((port) + 0x04)
#define GPIO_BSRR(port) REG32((port) + 0x10)
#define AFIO_MAPR REG32(0x40010004)

#define USART1_BASE UINT32_C(0x40013800)
#define USART_SR REG32(USART1_BASE + 0x00)
#define USART_DR REG32(USART1_BASE + 0x04)
#define USART_BRR REG32(USART1_BASE + 0x08)
#define USART_CR1 REG32(USART1_BASE + 0x0C)
#define USART_CR2 REG32(USART1_BASE + 0x10)
#define USART_CR3 REG32(USART1_BASE + 0x14)

#define FLASH_BASE UINT32_C(0x40022000)
#define FLASH_ACR REG32(FLASH_BASE + 0x00)
#define FLASH_KEYR REG32(FLASH_BASE + 0x04)
#define FLASH_SR REG32(FLASH_BASE + 0x0C)
#define FLASH_CR REG32(FLASH_BASE + 0x10)
#define FLASH_AR REG32(FLASH_BASE + 0x14)
#define FLASH_ERRORS (BIT(2) | BIT(4))
#define FLASH_BUSY BIT(0)
#define FLASH_LOCK BIT(7)
#define FLASH_VERIFY_ERROR BIT(29)
#define FLASH_LOCK_ERROR BIT(30)

#define IWDG_KR REG32(0x40003000)
#define SYSTICK_CTRL REG32(0xE000E010)
#define SYSTICK_LOAD REG32(0xE000E014)
#define SYSTICK_VAL REG32(0xE000E018)
#define SCB_ICSR REG32(0xE000ED04)
#define SCB_VTOR REG32(0xE000ED08)
#define SCB_AIRCR REG32(0xE000ED0C)
#define SCB_SCR REG32(0xE000ED10)
#define SCB_CCR REG32(0xE000ED14)
#define SCB_SHCSR REG32(0xE000ED24)
#define SCB_CFSR REG32(0xE000ED28)
#define SCB_HFSR REG32(0xE000ED2C)
#define SCB_DFSR REG32(0xE000ED30)
#define COREDEBUG_DEMCR REG32(0xE000EDFC)
#define DWT_CTRL REG32(0xE0001000)
#define DWT_CYCCNT REG32(0xE0001004)
#define CPU_HZ UINT32_C(8000000)
#define CYCLES_PER_MS (CPU_HZ / 1000U)

extern const uintptr_t vector_table[76];
extern const uint8_t __image_start[];

static volatile uint32_t milliseconds;
static uint32_t last_tick_cycles;
static bool runtime_ready;

static inline uint32_t irq_save(void)
{
    uint32_t saved;
    __asm volatile("mrs %0, primask\ncpsid i" : "=r"(saved) :: "memory");
    return saved;
}

static inline void irq_restore(uint32_t saved)
{
    __asm volatile("msr primask, %0" :: "r"(saved) : "memory");
}

static inline void barriers(void)
{
    __asm volatile("dsb\nisb" ::: "memory");
}

void platform_service(void)
{
    /* The reload key does not start an inactive IWDG. Do not write the start
     * key, change option bytes, or alter a retained watchdog's prescaler.
     */
    IWDG_KR = UINT32_C(0xAAAA);
}

static void gpio_mode(uint32_t port, unsigned pin, uint32_t mode)
{
    const unsigned shift = (pin & 7U) * 4U;
    volatile uint32_t *reg = pin < 8U ? &GPIO_CRL(port) : &GPIO_CRH(port);
    *reg = (*reg & ~(15U << shift)) | (mode << shift);
}

static void gpio_output(uint32_t port, unsigned pin, bool high)
{
    GPIO_BSRR(port) = high ? BIT(pin) : BIT(pin) << 16;
    gpio_mode(port, pin, 2U); /* 2 MHz general push-pull; preload first. */
}

void platform_safe_outputs(void)
{
    RCC_APB2ENR |= BIT(0) | BIT(2) | BIT(3) | BIT(4);
    (void)RCC_APB2ENR;
    __asm volatile("dsb" ::: "memory");
    /* Release PB3/PB4 from JTAG, keep SWD available. AFIO MAPR SWJ bits have
     * undefined readback, so explicitly replace all three SWJ bits. */
    AFIO_MAPR = (AFIO_MAPR & ~(7U << 24)) | (2U << 24);
    gpio_output(GPIO_C, 3, true);
    gpio_mode(GPIO_C, 13, 4U); /* Floating inputs; no probe pulses. */
    gpio_mode(GPIO_C, 14, 4U);
    gpio_output(GPIO_A, 0, false);
    gpio_output(GPIO_A, 1, false);
    gpio_output(GPIO_A, 7, false);
    gpio_output(GPIO_C, 0, false);
    gpio_output(GPIO_C, 2, false);
    gpio_output(GPIO_B, 8, false);
    gpio_output(GPIO_B, 6, false);
    gpio_output(GPIO_B, 4, false);
    gpio_output(GPIO_B, 9, false);
    gpio_output(GPIO_B, 7, false);
    gpio_output(GPIO_B, 5, false);
    gpio_output(GPIO_B, 3, false);
}

bool platform_board_supported(void)
{
    /* High-density STM32F103 device ID, and independently 512 KiB flash. */
    return (REG32(0xE0042000) & 0xFFFU) == 0x414U
        && REG16(0x1FFFF7E0) == 512U;
}

static void stop_dma(void)
{
    RCC_AHBENR |= BIT(0) | BIT(1);
    (void)RCC_AHBENR;
    for (unsigned channel = 0; channel < 7; ++channel)
        REG32(0x40020008U + channel * 20U) = 0;
    for (unsigned channel = 0; channel < 5; ++channel)
        REG32(0x40020408U + channel * 20U) = 0;
    REG32(0x40020004) = UINT32_MAX;
    REG32(0x40020404) = UINT32_MAX;
}

static void interrupt_cleanup(void)
{
    SYSTICK_CTRL = 0;
    SYSTICK_LOAD = 0;
    SYSTICK_VAL = 0;
    /* All architectural NVIC banks, including unused slots, are cleared.
     * Only SysTick will be enabled; USART and all other peripherals poll.
     */
    for (unsigned bank = 0; bank < 8; ++bank) {
        REG32(0xE000E180U + bank * 4U) = UINT32_MAX;
        REG32(0xE000E280U + bank * 4U) = UINT32_MAX;
    }
    for (unsigned word = 0; word < 60; ++word) {
        REG32(0xE000E400U + word * 4U) = 0;
    }
    REG32(0xE000ED18) = 0;
    REG32(0xE000ED1C) = 0;
    REG32(0xE000ED20) = 0;
    SCB_ICSR = BIT(25) | BIT(27); /* Clear pending SysTick and PendSV. */
    SCB_SHCSR = 0;
    SCB_CFSR = UINT32_MAX;
    SCB_HFSR = UINT32_MAX;
    SCB_DFSR = UINT32_MAX;
    SCB_SCR = 0;
    SCB_CCR = BIT(9); /* 8-byte exception stack alignment, reset defaults. */
    barriers();
}

/* Called before .data/.bss initialization. Keep this independent of RAM state. */
void platform_early_startup(void)
{
    barriers();
    SCB_VTOR = (uint32_t)(uintptr_t)vector_table;
    barriers();
    stop_dma();
    platform_safe_outputs();
    interrupt_cleanup();
}

__attribute__((noreturn)) static void init_failed(void)
{
    platform_safe_outputs();
    for (;;) {
        platform_service();
    }
}

static void wait_clock(uint32_t mask, uint32_t expected, bool use_cfgr)
{
    /* Early clock state is inherited, so use a bounded iteration count before
     * DWT timekeeping is established. A failed clock switch never permits IAP.
     */
    for (uint32_t attempt = 0; attempt < UINT32_C(2000000); ++attempt) {
        const uint32_t value = use_cfgr ? RCC_CFGR : RCC_CR;
        if ((value & mask) == expected) {
            return;
        }
        platform_service();
    }
    init_failed();
}

void platform_init(void)
{
    (void)irq_save();
    runtime_ready = false;
    platform_safe_outputs();
    interrupt_cleanup();
    SCB_VTOR = (uint32_t)(uintptr_t)vector_table;
    barriers();

    stop_dma();
    /* Reset inherited peripheral clients, excluding AFIO and GPIO. */
    RCC_APB1RSTR = UINT32_C(0x3AFEC9FF);
    RCC_APB1RSTR = 0;
    RCC_APB2RSTR = UINT32_C(0x0038FE00);
    RCC_APB2RSTR = 0;
    RCC_CR = (RCC_CR & ~(31U << 3)) | (16U << 3) | BIT(0);
    wait_clock(BIT(1), BIT(1), false);
    RCC_CFGR &= ~3U;
    wait_clock(3U << 2, 0, true);
    RCC_CFGR = 0; /* HCLK, APB1 and APB2 use HSI8 at divide by one. */
    RCC_CR &= ~(BIT(16) | BIT(18) | BIT(19) | BIT(24));
    wait_clock(BIT(25), 0, false);
    RCC_CIR = UINT32_C(0x009F0000);
    FLASH_ACR = 0; /* HSI8 permits zero flash wait states. */
    barriers();
    RCC_AHBENR = BIT(2) | BIT(4); /* SRAM / flash interfaces only. */
    RCC_APB1ENR = 0;
    RCC_APB2ENR = BIT(0) | BIT(2) | BIT(3) | BIT(4) | BIT(14);
    (void)RCC_APB2ENR;
    AFIO_MAPR = 2U << 24; /* Default USART1 PA9/PA10 mapping, SWD retained. */

    /* DWT is core-clocked even while flash is busy. SysTick uses it to recover
     * elapsed milliseconds after IRQ masking during an erase/program operation.
     */
    COREDEBUG_DEMCR |= BIT(24);
    DWT_CYCCNT = 0;
    DWT_CTRL |= BIT(0);
    const uint32_t initial_cycles = DWT_CYCCNT;
    for (volatile unsigned i = 0; i < 32; ++i) {
        __asm volatile("nop");
    }
    if ((DWT_CTRL & BIT(25)) != 0 || DWT_CYCCNT == initial_cycles) {
        init_failed();
    }
    milliseconds = 0;
    last_tick_cycles = DWT_CYCCNT;

    /* HSI8 / BRR69 = 115942 baud (+0.64% quantization, HSI tolerance
     * additional). PA9 alternate push-pull, PA10 input with pull-up. */
    GPIO_BSRR(GPIO_A) = BIT(9) | BIT(10);
    gpio_mode(GPIO_A, 9, 10U);
    gpio_mode(GPIO_A, 10, 8U);
    USART_CR1 = 0;
    USART_CR2 = 0;
    USART_CR3 = 0;
    USART_BRR = 69;
    (void)USART_SR;
    (void)USART_DR;
    USART_CR1 = BIT(13) | BIT(3) | BIT(2); /* UE, TE, RE only. */

    SYSTICK_LOAD = CYCLES_PER_MS - 1U;
    SYSTICK_VAL = 0;
    SYSTICK_CTRL = BIT(2) | BIT(1) | BIT(0);
    runtime_ready = true;
    platform_service();
    barriers();
    __asm volatile("cpsie i" ::: "memory");
}

static void advance_millis(void)
{
    const uint32_t elapsed = DWT_CYCCNT - last_tick_cycles;
    const uint32_t whole_ms = elapsed / CYCLES_PER_MS;
    last_tick_cycles += whole_ms * CYCLES_PER_MS;
    milliseconds += whole_ms;
}

void SysTick_Handler(void)
{
    advance_millis();
}

uint32_t platform_millis(void)
{
    const uint32_t saved = irq_save();
    advance_millis();
    const uint32_t result = milliseconds;
    irq_restore(saved);
    return result;
}

int platform_getchar(void)
{
    const uint32_t status = USART_SR;
    if ((status & 15U) != 0) {
        /* SR then DR clears PE/FE/NE/ORE. A damaged frame is never accepted. */
        (void)USART_DR;
        return -2;
    }
    if ((status & BIT(5)) == 0) {
        return -1;
    }
    return (int)(USART_DR & 0xFFU);
}

void platform_putchar(char value)
{
    const uint32_t started = platform_millis();
    while ((USART_SR & BIT(7)) == 0) {
        platform_service();
        if (platform_millis() - started >= 100U) {
            platform_reset();
        }
    }
    USART_DR = (uint8_t)value;
}

/* Both routines below and all their literal pools live in SRAM. While FLASH
 * BSY is set, there are no calls to flash, flash data reads, or SysTick waits.
 * IRQs are masked by the wrapper. CSS is disabled and no NMI source is enabled.
 * DWT deadlines keep running; IWDG is reloaded inside the SRAM polling loop.
 */
__attribute__((section(".ramfunc"), noinline))
static void flash_wait_ready(uint32_t timeout_cycles)
{
    const uint32_t started = DWT_CYCCNT;
    while ((FLASH_SR & FLASH_BUSY) != 0) {
        IWDG_KR = UINT32_C(0xAAAA);
        if (DWT_CYCCNT - started >= timeout_cycles) {
            /* Do not return to flash while it is still busy. Request reset
             * directly from SRAM; a partially written application stays invalid.
             */
            __asm volatile("dsb" ::: "memory");
            SCB_AIRCR = UINT32_C(0x05FA0004);
            __asm volatile("dsb" ::: "memory");
            for (;;) {
                __asm volatile("nop");
            }
        }
    }
}

__attribute__((section(".ramfunc"), noinline))
static uint32_t flash_operation(unsigned erase, uint32_t address, uint32_t value)
{
    if (address < PLATFORM_APPLICATION_START || address > PLATFORM_FLASH_END - 4U
        || (address & 3U) != 0 || (erase && (address & 2047U) != 0))
        return FLASH_VERIFY_ERROR;
    flash_wait_ready(CPU_HZ / 2U);
    if (FLASH_CR & FLASH_LOCK) {
        FLASH_KEYR = UINT32_C(0x45670123);
        FLASH_KEYR = UINT32_C(0xCDEF89AB);
    }
    if (FLASH_CR & FLASH_LOCK) return FLASH_LOCK_ERROR;
    FLASH_CR = 0;
    FLASH_SR = FLASH_ERRORS | BIT(5);
    uint32_t status = 0;
    if (erase) {
        FLASH_CR = BIT(1); /* Page erase only, never mass erase or options. */
        FLASH_AR = address;
        FLASH_CR |= BIT(6);
        __asm volatile("dsb" ::: "memory");
        flash_wait_ready(CPU_HZ / 2U);
        status = FLASH_SR & FLASH_ERRORS;
        FLASH_CR = 0;
    } else {
        for (unsigned half = 0; half < 2; ++half) {
            const uint16_t part = (uint16_t)(value >> (half * 16U));
            if (REG16(address + half * 2U) == part) continue;
            /* F1 programming requires erased halfwords (PM0075), unlike the
             * more permissive 1-to-0 check on the F4 implementation. */
            if (REG16(address + half * 2U) != UINT16_MAX) {
                status = FLASH_VERIFY_ERROR;
                break;
            }
            FLASH_CR = BIT(0);
            REG16(address + half * 2U) = part;
            __asm volatile("dsb" ::: "memory");
            flash_wait_ready(CPU_HZ / 10U);
            status = FLASH_SR & FLASH_ERRORS;
            FLASH_CR = 0;
            if (status != 0) break;
            IWDG_KR = UINT32_C(0xAAAA);
        }
    }
    FLASH_CR = FLASH_LOCK;
    FLASH_SR = FLASH_ERRORS | BIT(5);
    __asm volatile("dsb\nisb" ::: "memory");
    if (status != 0) return status;
    if (!erase) return REG32(address) == value ? 0 : FLASH_VERIFY_ERROR;
    for (uint32_t cursor = address; cursor < address + 2048U; cursor += 4U) {
        if (REG32(cursor) != UINT32_MAX) return FLASH_VERIFY_ERROR;
        IWDG_KR = UINT32_C(0xAAAA);
    }
    return 0;
}

static bool can_update(void)
{
    return runtime_ready && platform_board_supported()
        && (uintptr_t)__image_start == PLATFORM_UPDATER_START;
}

bool platform_erase_application(void)
{
    if (!can_update()) {
        return false;
    }
    platform_safe_outputs();
    for (uint32_t page = PLATFORM_APPLICATION_START; page < PLATFORM_FLASH_END; page += 2048U) {
        const uint32_t saved = irq_save();
        const uint32_t status = flash_operation(1, page, 0);
        irq_restore(saved);
        if (status != 0) {
            return false;
        }
    }
    return true;
}

bool platform_program_word(uint32_t address, uint32_t value)
{
    if (!can_update() || (address & 3U) != 0
        || address < PLATFORM_APPLICATION_START || address > PLATFORM_FLASH_END - 4U) {
        return false;
    }
    const uint32_t previous = REG32(address);
    if ((previous & value) != value) {
        return false; /* A requested 0-to-1 transition requires a page erase. */
    }
    if (previous == value) {
        return true;
    }
    const uint32_t saved = irq_save();
    const uint32_t status = flash_operation(0, address, value);
    irq_restore(saved);
    return status == 0;
}

uint32_t platform_read_word(uint32_t address)
{
    if ((address & 3U) != 0 || address < PLATFORM_FLASH_START
        || address > PLATFORM_FLASH_END - 4U) {
        return UINT32_MAX;
    }
    return REG32(address);
}

__attribute__((naked, noreturn, noinline))
static void jump_to_vectors(uint32_t stack __attribute__((unused)),
                            uint32_t entry __attribute__((unused)))
{
    /* No compiler-generated stack access is allowed after changing MSP. */
    __asm volatile(
        "msr msp, r0\n"
        "movs r2, #0\n"
        "msr control, r2\n"
        "msr basepri, r2\n"
        "msr faultmask, r2\n"
        "dsb\n"
        "isb\n"
        "bx r1\n");
}

__attribute__((noreturn)) void platform_boot_application(void)
{
    const uint32_t stack = platform_read_word(PLATFORM_APPLICATION_VECTORS);
    const uint32_t entry = platform_read_word(PLATFORM_APPLICATION_VECTORS + 4U);
    const uint32_t code_address = entry & ~1U;
    if (!runtime_ready || !platform_board_supported()
        || stack <= PLATFORM_SRAM_START || stack > PLATFORM_SRAM_END
        || (stack & 7U) != 0 || (entry & 1U) == 0
        || code_address < PLATFORM_APPLICATION_VECTORS + 484U
        || code_address >= PLATFORM_FLASH_END) {
        init_failed();
    }
    platform_safe_outputs();
    const uint32_t started = platform_millis();
    while ((USART_SR & BIT(6)) == 0 && platform_millis() - started < 100U) {
        platform_service();
    }
    (void)irq_save();
    USART_CR1 = 0;
    interrupt_cleanup();
    SCB_VTOR = PLATFORM_APPLICATION_VECTORS;
    barriers();
    platform_service();
    jump_to_vectors(stack, entry);
}

__attribute__((noreturn)) void platform_reset(void)
{
    if (runtime_ready) {
        const uint32_t started = platform_millis();
        while ((USART_SR & BIT(6)) == 0 && platform_millis() - started < 100U) {
            platform_service();
        }
    }
    (void)irq_save();
    platform_safe_outputs();
    barriers();
    SCB_AIRCR = UINT32_C(0x05FA0004);
    barriers();
    for (;;) {
        __asm volatile("nop");
    }
}
