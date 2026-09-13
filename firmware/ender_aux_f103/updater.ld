/* Preserve the first 28 KiB factory loader. Updater occupies 0x7000..0x10000. */
ENTRY(Reset_Handler)
MEMORY
{
    FLASH (rx) : ORIGIN = 0x08007000, LENGTH = 36K
    RAM (rwx) : ORIGIN = 0x20000000, LENGTH = 64K
}
/* STM32F103RET6 provides 64 KiB SRAM. This image requires that target. */
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
ASSERT(ADDR(.isr_vector) == 0x08007000, "Updater vector offset changed")
ASSERT(SIZEOF(.isr_vector) == 304, "STM32F103 vector table must contain 76 entries")
ASSERT(__flash_image_end <= 0x08010000, "Updater crosses into application sector")
ASSERT(_ebss <= _estack - 4096, "At least 4 KiB stack space is required")
