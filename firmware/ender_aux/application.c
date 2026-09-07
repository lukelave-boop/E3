#include "platform.h"
#include "protocol.h"

static char line[E3_LINE_CAPACITY];
static unsigned length;
static int discard;

static void send(const char *text) {
    while (*text) platform_putchar(*text++);
    platform_putchar('\n');
}

static int same(const char *a, const char *b) {
    while (*a && *a == *b) { ++a; ++b; }
    return *a == *b;
}

void application_init(void) {
    length = 0;
    discard = 0;
    platform_safe_outputs();
}

void application_receive(int byte) {
    if (byte == -1) return;
    if (byte < 0 || byte > 255) {
        platform_safe_outputs();
        discard = 1;
        length = 0;
        send("ERR UART");
        return;
    }
    if (byte == '\n') {
        if (discard) send("ERR LINE");
        else {
            if (length && line[length - 1] == '\r') --length;
            line[length] = 0;
            if (same(line, "INFO") || same(line, "M115"))
                send(E3_APPLICATION_ID);
            else if (same(line, "M5")) {
                platform_safe_outputs();
                send("OK M5 OUTPUTS_OFF");
            } else if (same(line, "UPDATE")) {
                platform_safe_outputs();
                send("OK UPDATE");
                platform_reset();
            } else send("ERR UNSUPPORTED");
        }
        discard = 0;
        length = 0;
    } else if (!discard) {
        if (length == E3_LINE_CAPACITY - 1 ||
            ((byte < 32 || byte > 126) && byte != '\r')) discard = 1;
        else line[length++] = (char)byte;
    }
}

#ifndef E3_CORE_TEST
int main(void) {
    platform_init();
    application_init();
    if (!platform_board_supported()) {
        send("ERR MCU_MISMATCH");
        for (;;) platform_service();
    }
    for (;;) {
        platform_service();
        application_receive(platform_getchar());
    }
}
#endif
