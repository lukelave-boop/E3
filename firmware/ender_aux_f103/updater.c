#include "platform.h"
#include "protocol.h"

int main(void) {
    platform_init();
    platform_safe_outputs();
    if (!platform_board_supported()) {
        const char *error = "ERR MCU_MISMATCH\n";
        while (*error) platform_putchar(*error++);
        for (;;) platform_service();
    }
    protocol_init();
    for (;;) {
        protocol_receive(platform_getchar());
        protocol_tick();
    }
}
