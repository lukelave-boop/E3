/* Rejected hardware remains queryable; it never reaches image or flash code. */
#include "diagnostic.h"
#include "platform.h"

static char request[8];
static unsigned used;
static int discard;

static void error(void)
{
    const char *text = "ERR MCU_MISMATCH\n";
    while (*text) platform_putchar(*text++);
    platform_report_hardware();
}

void diagnostic_init(void)
{
    used = 0;
    discard = 0;
    error();
}

static int same(const char *word)
{
    unsigned index = 0;
    while (word[index] && index < used && word[index] == request[index]) ++index;
    return index == used && !word[index];
}

void diagnostic_receive(int byte)
{
    if (byte == -1) return;
    if (byte < 0 || byte > 127) { discard = 1; return; }
    if (byte == '\r') return;
    if (byte == '\n') {
        if (!discard && (same("M115") || same("INFO") || same("DIAG"))) error();
        used = 0;
        discard = 0;
    } else if (!discard) {
        if (used == sizeof(request)) discard = 1;
        else request[used++] = (char)byte;
    }
}
