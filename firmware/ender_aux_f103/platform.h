#ifndef E3_ENDER_AUX_PLATFORM_H
#define E3_ENDER_AUX_PLATFORM_H

#include <stdbool.h>
#include <stdint.h>

/* This target is specifically STM32F103RET6 (512 KiB flash / 64 KiB SRAM). */
#define PLATFORM_FLASH_START UINT32_C(0x08000000)
#define PLATFORM_UPDATER_START UINT32_C(0x08007000)
#define PLATFORM_APPLICATION_START UINT32_C(0x08010000)
#define PLATFORM_APPLICATION_VECTORS UINT32_C(0x08010200)
#define PLATFORM_FLASH_END UINT32_C(0x08080000)
#define PLATFORM_SRAM_START UINT32_C(0x20000000)
#define PLATFORM_SRAM_END UINT32_C(0x20010000)

void platform_init(void);
void platform_safe_outputs(void);
bool platform_board_supported(void);
uint32_t platform_millis(void);
int platform_getchar(void); /* -1: no byte; -2: parity/framing/noise/overrun error. */
void platform_putchar(char value);
void platform_service(void); /* Reloads an already-running IWDG; never starts it. */

/* Only updater code may use these; neither writes below 0x08010000.
 * Erase covers 224 application pages of 2 KiB. No reception during erase.
 * Word programming uses two 16-bit operations, not an atomic 32-bit commit.
 */
bool platform_erase_application(void);
bool platform_program_word(uint32_t address, uint32_t value);
uint32_t platform_read_word(uint32_t address);

/* Caller must validate image length, target and complete-image integrity first.
 * The handoff independently checks hardware identity, SRAM/MSP and reset vector.
 */
#ifdef E3_CORE_TEST
#define PLATFORM_NORETURN
#else
#define PLATFORM_NORETURN __attribute__((noreturn))
#endif
PLATFORM_NORETURN void platform_boot_application(void);
PLATFORM_NORETURN void platform_reset(void);

#endif
