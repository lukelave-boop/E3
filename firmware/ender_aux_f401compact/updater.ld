/* Preserve Creality sectors 0-3. Updater owns only the separate sector 4. */
ENTRY(Reset_Handler)
MEMORY
{
    FLASH (rx) : ORIGIN = 0x08010000, LENGTH = 64K
    RAM (rwx) : ORIGIN = 0x20000000, LENGTH = 64K
}
/* Common 64 KiB SRAM layout for the supported F401RC and F401RE. */
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
ASSERT(ADDR(.isr_vector) == 0x08010000, "Updater vector offset changed")
ASSERT(SIZEOF(.isr_vector) == 404, "STM32F401 vector table must contain 101 entries")
ASSERT(__flash_image_end <= 0x08020000, "Updater crosses into application sector")
ASSERT(_ebss <= _estack - 4096, "At least 4 KiB stack space is required")
