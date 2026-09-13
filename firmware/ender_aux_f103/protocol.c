#include "protocol.h"
#include "image.h"
#include "platform.h"

static char line[E3_LINE_CAPACITY];
static uint32_t line_length;
static uint32_t started_at;
static uint32_t payload_length;
static uint32_t payload_crc;
static uint32_t next_offset;
static int automatic_boot;
static int session_active;
static int discarding;

static void send(const char *text) {
    while (*text) platform_putchar(*text++);
    platform_putchar('\n');
}

static int same(const char *a, const char *b) {
    while (*a && *a == *b) { ++a; ++b; }
    return *a == *b;
}

static int hex_digit(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    return -1;
}

static int hex32(const char *text, uint32_t *out) {
    uint32_t value = 0;
    for (uint32_t i = 0; i < 8; ++i) {
        int digit = hex_digit(text[i]);
        if (digit < 0) return 0;
        value = (value << 4) | (uint32_t)digit;
    }
    *out = value;
    return 1;
}

static void abort_with(const char *reason) {
    session_active = 0;
    send(reason);
}

static void begin(void) {
    uint32_t board, length, crc;
    /* All syntax and range checks precede the first destructive operation. */
    if (line_length != 32 || line[14] != ' ' || line[23] != ' ' ||
        !hex32(line + 6, &board) || !hex32(line + 15, &length) ||
        !hex32(line + 24, &crc)) {
        abort_with("ERR BEGIN");
        return;
    }
    if (!image_metadata_valid(board, length)) {
        abort_with("ERR IMAGE");
        return;
    }
    /* Only a validated update request may cancel the normal boot deadline.
     * Stay here even if erase fails; a partial erase must never boot. */
    automatic_boot = 0;
    session_active = 0;
    if (!platform_erase_application()) {
        send("ERR ERASE");
        return;
    }
    /* Verify the erase rather than relying exclusively on a status flag. */
    for (uint32_t i = 0; i < E3_HEADER_SIZE + E3_PAYLOAD_CAPACITY; i += 4) {
        if (!(i & 1023)) platform_service();
        if (platform_read_word(E3_HEADER_ADDRESS + i) != UINT32_C(0xFFFFFFFF)) {
            send("ERR ERASE");
            return;
        }
    }
    payload_length = length;
    payload_crc = crc;
    next_offset = 0;
    session_active = 1;
    send("OK BEGIN");
}

static void data(void) {
    uint32_t offset;
    uint8_t bytes[64];
    if (!session_active) { send("ERR SESSION"); return; }
    if (line_length < 22 || line_length > 142 || line[13] != ' ' ||
        !hex32(line + 5, &offset) || ((line_length - 14) & 7)) {
        abort_with("ERR DATA");
        return;
    }
    uint32_t count = (line_length - 14) / 2;
    if (offset != next_offset || count > payload_length - next_offset) {
        abort_with("ERR OFFSET");
        return;
    }
    for (uint32_t i = 0; i < count; ++i) {
        int hi = hex_digit(line[14 + 2 * i]);
        int lo = hex_digit(line[15 + 2 * i]);
        if (hi < 0 || lo < 0) { abort_with("ERR DATA"); return; }
        bytes[i] = (uint8_t)((hi << 4) | lo);
    }
    for (uint32_t i = 0; i < count; i += 4) {
        uint32_t word = (uint32_t)bytes[i] | ((uint32_t)bytes[i + 1] << 8) |
                        ((uint32_t)bytes[i + 2] << 16) |
                        ((uint32_t)bytes[i + 3] << 24);
        uint32_t address = E3_PAYLOAD_ADDRESS + offset + i;
        if (!platform_program_word(address, word) ||
            platform_read_word(address) != word) {
            abort_with("ERR WRITE");
            return;
        }
    }
    next_offset += count;
    char reply[] = "OK DATA 00000000";
    const char digits[] = "0123456789ABCDEF";
    for (uint32_t i = 0; i < 8; ++i)
        reply[8 + i] = digits[(next_offset >> (28 - 4 * i)) & 15];
    send(reply);
}

static int prefix(const char *value, const char *start) {
    while (*start) if (*value++ != *start++) return 0;
    return 1;
}

static void command(void) {
    if (same(line, "INFO") || same(line, "M115")) send(E3_UPDATER_ID);
    else if (same(line, "HOLD")) {
        automatic_boot = 0;
        send("OK HOLD");
    }
    else if (prefix(line, "BEGIN ")) begin();
    else if (prefix(line, "DATA ")) data();
    else if (same(line, "END")) {
        if (!session_active) { send("ERR SESSION"); return; }
        session_active = 0;
        if (next_offset != payload_length) { send("ERR INCOMPLETE"); return; }
        if (!image_validate_payload(payload_length, payload_crc)) {
            send("ERR IMAGE");
            return;
        }
        if (!image_commit(payload_length, payload_crc)) {
            send("ERR COMMIT");
            return;
        }
        send("OK END");
    } else if (same(line, "BOOT")) {
        if (session_active) { abort_with("ERR SESSION"); return; }
        if (!image_validate()) { send("ERR IMAGE"); return; }
        send("OK BOOT");
        platform_boot_application();
    } else abort_with("ERR UNSUPPORTED");
}

void protocol_init(void) {
    line_length = 0;
    discarding = 0;
    session_active = 0;
    next_offset = 0;
    payload_length = 0;
    payload_crc = 0;
    automatic_boot = image_validate();
    started_at = platform_millis();
}

void protocol_receive(int byte) {
    if (byte == -1) return;
    /* Ordinary queries, partial lines and UART errors do not hold startup. */
    if (byte < 0 || byte > 255) {
        session_active = 0;
        line_length = 0;
        discarding = 1;
        send("ERR UART");
        return;
    }
    if (byte == '\n') {
        if (discarding) {
            session_active = 0;
            send("ERR LINE");
        } else {
            if (line_length && line[line_length - 1] == '\r') --line_length;
            line[line_length] = 0;
            command();
        }
        line_length = 0;
        discarding = 0;
        return;
    }
    if (discarding) return;
    if (line_length == E3_LINE_CAPACITY - 1 ||
        ((byte < 32 || byte > 126) && byte != '\r')) {
        session_active = 0;
        discarding = 1;
        return;
    }
    line[line_length++] = (char)byte;
}

void protocol_tick(void) {
    platform_service();
    if (automatic_boot && (uint32_t)(platform_millis() - started_at) >= 5000) {
        automatic_boot = 0;
        if (image_validate()) platform_boot_application();
    }
}
