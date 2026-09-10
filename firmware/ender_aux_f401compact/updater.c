#include "platform.h"
#include "protocol.h"
#include "diagnostic.h"

int main(void) {
    platform_init();
    platform_safe_outputs();
    if (!platform_board_supported()) {
        diagnostic_init();
        for (;;) {
            platform_service();
            diagnostic_receive(platform_getchar());
        }
    }
    protocol_init();
    for (;;) {
        protocol_receive(platform_getchar());
        protocol_tick();
    }
}
