/* The first 512 bytes of sector 5 belong to the packager's integrity header.
 * This ELF/bin contains only the payload, starting with vectors at +0x200.
 */
ENTRY(Reset_Handler)
MEMORY
{
    FLASH (rx) : ORIGIN = 0x08020200, LENGTH = 384K - 512
    RAM (rwx) : ORIGIN = 0x20000000, LENGTH = 96K
}
/* Keep the initial stack inside the common 64 KiB to permit an explicit MCU
 * mismatch report on smaller F401 parts. RET6 / 512 KiB is still required.
 */
_estack = ORIGIN(RAM) + 64K;
__image_start = ORIGIN(FLASH);
SECTIONS
{
    .isr_vector ORIGIN(FLASH) : ALIGN(512)
    {
        KEEP(*(.isr_vector))
    } > FLASH
    .text : ALIGN(4)
    {
        *(.text .text.*)
        *(.rodata .rodata.*)
        . = ALIGN(4);
    } > FLASH
    .ARM.extab : { *(.ARM.extab* .gnu.linkonce.armextab.*) } > FLASH
    .ARM.exidx :
    {
        __exidx_start = .;
        *(.ARM.exidx* .gnu.linkonce.armexidx.*)
        __exidx_end = .;
    } > FLASH
    .data : ALIGN(4)
    {
        _sdata = .;
        __ramfunc_start = .;
        *(.ramfunc .ramfunc.*)
        __ramfunc_end = .;
        *(.data .data.*)
        . = ALIGN(4);
        _edata = .;
    } > RAM AT > FLASH
    _sidata = LOADADDR(.data);
    __flash_image_end = LOADADDR(.data) + SIZEOF(.data);
    .bss (NOLOAD) : ALIGN(4)
    {
        _sbss = .;
        *(.bss .bss.* COMMON)
        . = ALIGN(4);
        _ebss = .;
    } > RAM
    /DISCARD/ : { *(.comment) *(.note*) *(.eh_frame*) }
}
ASSERT(ADDR(.isr_vector) == 0x08020200, "Application vector offset changed")
ASSERT(SIZEOF(.isr_vector) == 404, "STM32F401 vector table must contain 101 entries")
ASSERT(__flash_image_end <= 0x08080000, "Application exceeds STM32F401RET6 flash")
ASSERT(_ebss <= _estack - 4096, "At least 4 KiB stack space is required")
