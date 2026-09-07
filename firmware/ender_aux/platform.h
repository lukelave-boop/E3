#ifndef E3_ENDER_AUX_PLATFORM_H
#define E3_ENDER_AUX_PLATFORM_H

#include <stdbool.h>
#include <stdint.h>

/* This target is specifically STM32F401RET6 (512 KiB flash / 96 KiB SRAM). */
#define PLATFORM_FLASH_START UINT32_C(0x08000000)
#define PLATFORM_UPDATER_START UINT32_C(0x08010000)
#define PLATFORM_APPLICATION_START UINT32_C(0x08020000)
#define PLATFORM_APPLICATION_VECTORS UINT32_C(0x08020200)
#define PLATFORM_FLASH_END UINT32_C(0x08080000)
#define PLATFORM_SRAM_START UINT32_C(0x20000000)
#define PLATFORM_SRAM_END UINT32_C(0x20018000)

void platform_init(void);
void platform_safe_outputs(void);
bool platform_board_supported(void);
uint32_t platform_millis(void);
int platform_getchar(void); /* -1: no byte; -2: parity/framing/noise/overrun error. */
void platform_putchar(char value);
void platform_service(void); /* Reloads an already-running IWDG; never starts it. */

/* Only updater code may use these; neither function writes sectors 0 through 4.
 * Erase may take 24 seconds before its timeouts. No reception occurs during erase.
 * Word programming uses four x8 operations, not an atomic 32-bit commit.
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
