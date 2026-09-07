/* Freestanding tests: run tests_main() under an ARM emulator, returning zero
 * on success or the failing source line. No peripheral registers are touched. */
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "image.h"
#include "platform.h"
#include "protocol.h"

#define FLASH_WORDS ((E3_HEADER_SIZE + E3_PAYLOAD_CAPACITY) / 4)
#define CHECK(value) do { if (!(value)) return __LINE__; } while (0)
#define FULL_DATA_REPLIES "OK DATA 00000040\nOK DATA 00000080\n" \
    "OK DATA 000000C0\nOK DATA 00000100\nOK DATA 00000140\n" \
    "OK DATA 00000180\nOK DATA 000001C0\nOK DATA 00000200\n"

/* GCC may lower local aggregate initialization to these routines even for a
 * freestanding test translation unit. Volatile stores prevent recursive
 * lowering back into the same helpers. The production images do not link this. */
void *memset(void *destination, int value, size_t count) {
    volatile unsigned char *out = destination;
    for (size_t i = 0; i < count; ++i) out[i] = (unsigned char)value;
    return destination;
}

void *memcpy(void *destination, const void *source, size_t count) {
    volatile unsigned char *out = destination;
    const volatile unsigned char *in = source;
    for (size_t i = 0; i < count; ++i) out[i] = in[i];
    return destination;
}

static uint32_t flash[FLASH_WORDS];
static char output[4096];
static uint32_t output_length;
static uint32_t now;
static uint32_t erased;
static uint32_t programmed;
static uint32_t booted;
static uint32_t resets;
static uint32_t off_calls;
static uint32_t fail_program;
static uint32_t partial_program_bytes;
static uint32_t write_addresses[512];
static int fail_erase;
static int partial_erase;
static int corrupt_program;
static int illegal_access;

void platform_init(void) {}
void platform_safe_outputs(void) { ++off_calls; }
bool platform_board_supported(void) { return true; }
uint32_t platform_millis(void) { return now; }
int platform_getchar(void) { return -1; }
void platform_putchar(char byte) {
    if (output_length + 1 < sizeof(output)) {
        output[output_length++] = byte;
        output[output_length] = 0;
    }
}
void platform_service(void) {}
void platform_reset(void) { ++resets; }
void platform_boot_application(void) { ++booted; }

bool platform_erase_application(void) {
    ++erased;
    if (fail_erase) return false;
    for (uint32_t i = 0; i < FLASH_WORDS; ++i) flash[i] = UINT32_C(0xFFFFFFFF);
    if (partial_erase) flash[FLASH_WORDS - 1] = 0;
    return true;
}

static int valid_address(uint32_t address) {
    return !(address & 3) && address >= E3_HEADER_ADDRESS &&
           address < E3_HEADER_ADDRESS + E3_HEADER_SIZE + E3_PAYLOAD_CAPACITY;
}

uint32_t platform_read_word(uint32_t address) {
    if (!valid_address(address)) { illegal_access = 1; return 0; }
    return flash[(address - E3_HEADER_ADDRESS) / 4];
}

bool platform_program_word(uint32_t address, uint32_t word) {
    ++programmed;
    if (!valid_address(address)) { illegal_access = 1; return false; }
    if (programmed <= 512) write_addresses[programmed - 1] = address;
    uint32_t index = (address - E3_HEADER_ADDRESS) / 4;
    if (fail_program && programmed == fail_program) {
        /* STM32 implementation writes bytes: interrupted words may be torn. */
        for (uint32_t i = 0; i < partial_program_bytes; ++i) {
            uint32_t mask = UINT32_C(0xFF) << (8 * i);
            flash[index] &= word | ~mask;
        }
        return false;
    }
    if ((flash[index] & word) != word) return false;
    flash[index] &= word;
    if (corrupt_program) flash[index] = 0;
    return true;
}

static int same(const char *a, const char *b) {
    while (*a && *a == *b) { ++a; ++b; }
    return *a == *b;
}

static void clear_output(void) { output_length = 0; output[0] = 0; }
static void send(const char *text) {
    while (*text) protocol_receive((unsigned char)*text++);
}

static void hex32(char *text, uint32_t value) {
    const char hex[] = "0123456789ABCDEF";
    for (uint32_t i = 0; i < 8; ++i)
        text[i] = hex[(value >> (28 - 4 * i)) & 15];
}

static void store(uint8_t *bytes, uint32_t value) {
    for (uint32_t i = 0; i < 4; ++i) bytes[i] = (uint8_t)(value >> (8 * i));
}

static void setup(void) {
    for (uint32_t i = 0; i < FLASH_WORDS; ++i) flash[i] = UINT32_C(0xFFFFFFFF);
    clear_output();
    now = erased = programmed = booted = fail_program = 0;
    off_calls = resets = 0;
    partial_program_bytes = 0;
    fail_erase = partial_erase = corrupt_program = illegal_access = 0;
    protocol_init();
}

static void payload(uint8_t *bytes, uint32_t stack, uint32_t reset) {
    for (uint32_t i = 0; i < 512; i += 4) store(bytes + i, UINT32_C(0xE7FEE7FE));
    store(bytes, stack);
    store(bytes + 4, reset);
}

static void seed_payload(const uint8_t *bytes, uint32_t length) {
    for (uint32_t i = 0; i < length; i += 4) {
        flash[(E3_HEADER_SIZE + i) / 4] = (uint32_t)bytes[i] |
            ((uint32_t)bytes[i + 1] << 8) | ((uint32_t)bytes[i + 2] << 16) |
            ((uint32_t)bytes[i + 3] << 24);
    }
}

static void begin(uint32_t board, uint32_t length, uint32_t crc) {
    char text[] = "BEGIN 00000000 00000000 00000000\n";
    hex32(text + 6, board);
    hex32(text + 15, length);
    hex32(text + 24, crc);
    send(text);
}

static void data(uint32_t offset, const uint8_t *bytes, uint32_t length) {
    char text[144] = "DATA 00000000 ";
    const char hex[] = "0123456789ABCDEF";
    hex32(text + 5, offset);
    for (uint32_t i = 0; i < length; ++i) {
        text[14 + 2 * i] = hex[bytes[i] >> 4];
        text[15 + 2 * i] = hex[bytes[i] & 15];
    }
    text[14 + 2 * length] = '\n';
    text[15 + 2 * length] = 0;
    send(text);
}

static void all_data(const uint8_t *bytes, uint32_t length) {
    for (uint32_t offset = 0; offset < length; offset += 64) {
        uint32_t count = length - offset;
        if (count > 64) count = 64;
        data(offset, bytes + offset, count);
    }
}

static void make_header(uint8_t *header, uint32_t length, uint32_t crc) {
    for (uint32_t i = 0; i < E3_HEADER_SIZE; ++i) header[i] = 0xFF;
    store(header, E3_IMAGE_MAGIC);
    store(header + 4, E3_IMAGE_FORMAT);
    store(header + 8, E3_BOARD_ID);
    store(header + 12, length);
    store(header + 16, crc);
    store(header + 20, E3_IMAGE_VERSION);
    store(header + 24, image_crc32(header + 4, 20));
}

static int test_crc_and_header(void) {
    uint8_t header[E3_HEADER_SIZE];
    CHECK(image_crc32((const uint8_t *)"123456789", 9) == UINT32_C(0xCBF43926));
    CHECK(image_crc32((const uint8_t *)"", 0) == 0);
    make_header(header, 512, 0);
    CHECK(image_validate_header(header));
    const uint32_t fields[] = {0, 4, 8, 12, 20};
    const uint32_t invalid[] = {0, 2, 0, 0, 2};
    for (uint32_t i = 0; i < 5; ++i) {
        make_header(header, 512, 0);
        store(header + fields[i], invalid[i]);
        store(header + 24, image_crc32(header + 4, 20));
        CHECK(!image_validate_header(header));
    }
    make_header(header, 512, 0);
    header[24] ^= 1;
    CHECK(!image_validate_header(header));
    make_header(header, 512, 0);
    header[511] = 0;
    CHECK(!image_validate_header(header));
    CHECK(!image_metadata_valid(E3_BOARD_ID, 4));
    CHECK(!image_metadata_valid(E3_BOARD_ID, 9));
    CHECK(!image_metadata_valid(E3_BOARD_ID, E3_PAYLOAD_CAPACITY + 4));
    CHECK(image_metadata_valid(E3_BOARD_ID, E3_PAYLOAD_CAPACITY));
    CHECK(!image_metadata_valid(E3_BOARD_ID, 8));
    CHECK(!image_metadata_valid(E3_BOARD_ID, 404));
    CHECK(image_metadata_valid(E3_BOARD_ID, 408));
    return 0;
}

static int test_vectors_and_commit(void) {
    uint8_t bytes[512];
    setup();
    CHECK(!image_validate());
    payload(bytes, E3_SRAM_END, E3_PAYLOAD_ADDRESS + 405);
    seed_payload(bytes, 512);
    uint32_t crc = image_crc32(bytes, 512);
    CHECK(image_validate_payload(512, crc));
    CHECK(!image_validate_payload(512, crc ^ 1));
    CHECK(!image_validate_payload(15, crc));
    CHECK(!image_validate_payload(E3_PAYLOAD_CAPACITY + 4, crc));
    CHECK(image_commit(512, crc));
    CHECK(programmed == 7);
    CHECK(write_addresses[6] == E3_HEADER_ADDRESS);
    CHECK(image_validate());
    CHECK(!image_commit(512, crc));
    flash[(E3_HEADER_SIZE + 12) / 4] ^= 1;
    CHECK(!image_validate());

    const uint32_t bad_stack[] = {E3_SRAM_START, E3_SRAM_START - 8,
                                 E3_SRAM_END + 8, E3_SRAM_END - 1};
    for (uint32_t i = 0; i < 4; ++i) {
        setup();
        payload(bytes, bad_stack[i], E3_PAYLOAD_ADDRESS + 405);
        seed_payload(bytes, 512);
        CHECK(!image_validate_payload(512, image_crc32(bytes, 512)));
        CHECK(!image_commit(512, image_crc32(bytes, 512)));
        CHECK(programmed == 0);
    }
    const uint32_t bad_reset[] = {E3_PAYLOAD_ADDRESS + 404, E3_PAYLOAD_ADDRESS - 1,
                                 E3_PAYLOAD_ADDRESS + 513, UINT32_C(0xFFFFFFFF),
                                 E3_PAYLOAD_ADDRESS + 403};
    for (uint32_t i = 0; i < 5; ++i) {
        setup();
        payload(bytes, E3_SRAM_END, bad_reset[i]);
        seed_payload(bytes, 512);
        CHECK(!image_validate_payload(512, image_crc32(bytes, 512)));
    }
    for (uint32_t failure = 1; failure <= 7; ++failure) {
        for (uint32_t torn_bytes = 0; torn_bytes < 4; ++torn_bytes) {
            setup();
            payload(bytes, E3_SRAM_END, E3_PAYLOAD_ADDRESS + 405);
            seed_payload(bytes, 512);
            fail_program = failure;
            partial_program_bytes = torn_bytes;
            CHECK(!image_commit(512, image_crc32(bytes, 512)));
            CHECK(!image_validate());
            CHECK(flash[0] != E3_IMAGE_MAGIC);
            protocol_init();
            now = 6000;
            protocol_tick();
            CHECK(!booted);
        }
    }
    setup();
    payload(bytes, E3_SRAM_END, E3_PAYLOAD_ADDRESS + 405);
    seed_payload(bytes, 512);
    corrupt_program = 1;
    CHECK(!image_commit(512, image_crc32(bytes, 512)));
    CHECK(!image_validate());
    CHECK(flash[0] == UINT32_C(0xFFFFFFFF));
    CHECK(!illegal_access);
    return 0;
}

static int test_valid_upload_and_boot(void) {
    uint8_t bytes[512];
    setup();
    send("HOLD\r\nINFO\n");
    CHECK(same(output, "OK HOLD\n" E3_UPDATER_ID "\n"));
    payload(bytes, E3_SRAM_END, E3_PAYLOAD_ADDRESS + 405);
    clear_output();
    begin(E3_BOARD_ID, 512, image_crc32(bytes, 512));
    CHECK(same(output, "OK BEGIN\n"));
    CHECK(erased == 1 && programmed == 0);
    clear_output();
    data(0, bytes, 4);
    CHECK(same(output, "OK DATA 00000004\n"));
    clear_output();
    data(4, bytes + 4, 60);
    for (uint32_t offset = 64; offset < 512; offset += 64)
        data(offset, bytes + offset, 64);
    CHECK(same(output, FULL_DATA_REPLIES));
    CHECK(!image_validate());
    CHECK(flash[0] == UINT32_C(0xFFFFFFFF));
    clear_output();
    send("END\n");
    CHECK(same(output, "OK END\n"));
    CHECK(image_validate());
    CHECK(write_addresses[programmed - 1] == E3_HEADER_ADDRESS);
    now = 6000;
    protocol_tick();
    CHECK(booted == 0);
    clear_output();
    send("BOOT\n");
    CHECK(same(output, "OK BOOT\n"));
    CHECK(booted == 1 && !illegal_access);
    return 0;
}

static int test_begin_rejections(void) {
    setup();
    const uint32_t bad_length[] = {0, 404, 409, E3_PAYLOAD_CAPACITY + 4,
                                   UINT32_C(0xFFFFFFFC)};
    for (uint32_t i = 0; i < 5; ++i) {
        clear_output();
        begin(E3_BOARD_ID, bad_length[i], 0);
        CHECK(same(output, "ERR IMAGE\n"));
        CHECK(erased == 0 && programmed == 0);
    }
    clear_output();
    begin(E3_BOARD_ID ^ 1, 512, 0);
    CHECK(same(output, "ERR IMAGE\n"));
    const char *bad[] = {
        "BEGIN 0401E013 00000010 0000000G\n",
        "BEGIN 0401E013 00000010 0000000\n",
        "BEGIN 0401E013 00000010 000000000\n",
        "BEGIN 0401E013X00000010 00000000\n",
        "BEGIN 0401E013 00000010X00000000\n",
        "BEGIN 0401E013 00000010 -0000000\n",
    };
    for (uint32_t i = 0; i < sizeof(bad) / sizeof(bad[0]); ++i) {
        clear_output();
        send(bad[i]);
        CHECK(same(output, "ERR BEGIN\n"));
    }
    CHECK(erased == 0 && programmed == 0 && !illegal_access);
    fail_erase = 1;
    clear_output();
    begin(E3_BOARD_ID, 512, 0);
    CHECK(same(output, "ERR ERASE\n"));
    send("DATA 00000000 00000000\n");
    CHECK(programmed == 0);
    setup();
    partial_erase = 1;
    begin(E3_BOARD_ID, 512, 0);
    CHECK(same(output, "ERR ERASE\n"));
    CHECK(programmed == 0);
    return 0;
}

static int test_data_and_end_rejections(void) {
    uint8_t bytes[512];
    payload(bytes, E3_SRAM_END, E3_PAYLOAD_ADDRESS + 405);
    const char *bad[] = {
        "DATA 00000000 0000000G\n", "DATA 00000000 000000\n",
        "DATA 00000000 000000000\n", "DATA 0000000Z 00000000\n",
        "DATA 00000000X00000000\n", "DATA 00000000 \n",
    };
    for (uint32_t i = 0; i < sizeof(bad) / sizeof(bad[0]); ++i) {
        setup();
        begin(E3_BOARD_ID, 512, image_crc32(bytes, 512));
        clear_output();
        send(bad[i]);
        CHECK(same(output, "ERR DATA\n"));
        CHECK(programmed == 0);
        clear_output();
        data(0, bytes, 16);
        CHECK(same(output, "ERR SESSION\n"));
        CHECK(!image_validate());
    }
    setup();
    begin(E3_BOARD_ID, 512, image_crc32(bytes, 512));
    clear_output();
    data(4, bytes, 4);
    CHECK(same(output, "ERR OFFSET\n"));
    CHECK(programmed == 0);
    setup();
    begin(E3_BOARD_ID, 512, image_crc32(bytes, 512));
    data(0, bytes, 16);
    clear_output();
    data(0, bytes, 4);
    CHECK(same(output, "ERR OFFSET\n"));
    send("END\n");
    CHECK(!image_validate());

    setup();
    begin(E3_BOARD_ID, 408, 0);
    all_data(bytes, 384);
    clear_output();
    data(384, bytes + 384, 28);
    CHECK(same(output, "ERR OFFSET\n") && programmed == 96);

    setup();
    begin(E3_BOARD_ID, 512, image_crc32(bytes, 512));
    data(0, bytes, 4);
    clear_output();
    send("END\n");
    CHECK(same(output, "ERR INCOMPLETE\n"));
    CHECK(programmed == 1 && !image_validate());
    clear_output();
    data(4, bytes + 4, 12);
    CHECK(same(output, "ERR SESSION\n"));

    setup();
    begin(E3_BOARD_ID, 512, image_crc32(bytes, 512) ^ 1);
    all_data(bytes, 512);
    clear_output();
    send("END\n");
    CHECK(same(output, "ERR IMAGE\n"));
    CHECK(programmed == 128 && !image_validate());
    setup();
    begin(E3_BOARD_ID, 512, image_crc32(bytes, 512));
    fail_program = 2;
    data(0, bytes, 16);
    CHECK(same(output, "OK BEGIN\nERR WRITE\n"));
    clear_output();
    send("END\n");
    CHECK(same(output, "ERR SESSION\n"));
    CHECK(!image_validate());
    setup();
    begin(E3_BOARD_ID, 512, image_crc32(bytes, 512));
    corrupt_program = 1;
    data(0, bytes, 16);
    CHECK(same(output, "OK BEGIN\nERR WRITE\n"));
    CHECK(!image_validate() && !illegal_access);
    return 0;
}

static int test_input_and_startup(void) {
    uint8_t bytes[512];
    payload(bytes, E3_SRAM_END, E3_PAYLOAD_ADDRESS + 405);
    setup();
    now = 10000;
    protocol_tick();
    CHECK(booted == 0);
    send("BOOT\n");
    CHECK(same(output, "ERR IMAGE\n"));
    setup();
    seed_payload(bytes, 512);
    CHECK(image_commit(512, image_crc32(bytes, 512)));
    protocol_init();
    now = 4999;
    protocol_tick();
    CHECK(booted == 0);
    now = 5000;
    protocol_tick();
    CHECK(booted == 1);
    protocol_tick();
    CHECK(booted == 1);
    protocol_init();
    protocol_receive('I');
    now += 6000;
    protocol_tick();
    CHECK(booted == 1); /* Even an incomplete command cancels autoboot. */
    protocol_init();
    send("HOLD\n");
    now += 6000;
    protocol_tick();
    CHECK(booted == 1);
    protocol_init();
    send("INFO\n");
    now += 6000;
    protocol_tick();
    CHECK(booted == 1);
    protocol_init();
    begin(E3_BOARD_ID ^ 1, 512, 0);
    now += 6000;
    protocol_tick();
    CHECK(booted == 1 && erased == 0 && image_validate());

    setup();
    begin(E3_BOARD_ID, 512, image_crc32(bytes, 512));
    clear_output();
    for (uint32_t i = 0; i < 300; ++i) protocol_receive('A');
    send("INFO\nINFO\n");
    CHECK(same(output, "ERR LINE\n" E3_UPDATER_ID "\n"));
    clear_output();
    data(0, bytes, 16);
    CHECK(same(output, "ERR SESSION\n"));
    CHECK(programmed == 0);
    setup();
    begin(E3_BOARD_ID, 512, image_crc32(bytes, 512));
    clear_output();
    send("DA");
    protocol_receive(-2);
    send("TA 00000000 00000000\nINFO\n");
    CHECK(same(output, "ERR UART\nERR LINE\n" E3_UPDATER_ID "\n"));
    CHECK(programmed == 0);
    clear_output();
    protocol_receive(0);
    send("INFO\nINFO\n");
    CHECK(same(output, "ERR LINE\n" E3_UPDATER_ID "\n"));
    setup();
    send("M3\nG0 X1\nM280 P0 S10\n");
    CHECK(same(output, "ERR UNSUPPORTED\nERR UNSUPPORTED\nERR UNSUPPORTED\n"));
    CHECK(erased == 0 && programmed == 0 && booted == 0 && !illegal_access);
    return 0;
}

static int test_interruption_and_recovery(void) {
    uint8_t bytes[512];
    payload(bytes, E3_SRAM_END, E3_PAYLOAD_ADDRESS + 405);
    uint32_t crc = image_crc32(bytes, 512);
    for (uint32_t count = 0; count <= 512; count += 64) {
        setup();
        begin(E3_BOARD_ID, 512, crc);
        all_data(bytes, count);
        /* Simulate reset/power loss, including after last DATA but before END. */
        CHECK(!image_validate());
        protocol_init();
        now = 6000;
        protocol_tick();
        CHECK(booted == 0);
        clear_output();
        send("END\n");
        CHECK(same(output, "ERR SESSION\n"));
        clear_output();
        begin(E3_BOARD_ID, 512, crc);
        all_data(bytes, 512);
        send("END\n");
        CHECK(same(output, "OK BEGIN\n" FULL_DATA_REPLIES "OK END\n"));
        CHECK(image_validate() && erased == 2 && !illegal_access);
    }
    /* The deadline subtraction also behaves across millisecond counter wrap. */
    setup();
    seed_payload(bytes, 512);
    CHECK(image_commit(512, crc));
    now = UINT32_C(0xFFFFFF00);
    protocol_init();
    now += 4999;
    protocol_tick();
    CHECK(booted == 0);
    ++now;
    protocol_tick();
    CHECK(booted == 1);
    return 0;
}

static void app_send(const char *text) {
    while (*text) application_receive((unsigned char)*text++);
}

static int test_application(void) {
    setup();
    application_init();
    CHECK(off_calls == 1);
    app_send("INFO\nM115\r\n");
    CHECK(same(output, E3_APPLICATION_ID "\n" E3_APPLICATION_ID "\n"));
    CHECK(off_calls == 1 && resets == 0);
    clear_output();
    app_send("M5\n");
    CHECK(same(output, "OK M5 OUTPUTS_OFF\n") && off_calls == 2);
    clear_output();
    app_send("UPDATE\n");
    CHECK(same(output, "OK UPDATE\n") && off_calls == 3 && resets == 1);

    const char *rejected[] = {
        "M3 S1\n", "M4 S1\n", "G0 Z1\n", "G1 Z1\n", "G28\n", "G30\n",
        "M280 P0 S10\n", "M106 S255\n", "M104 S200\n", "M140 S60\n",
        "BEGIN 0401E013 00000010 00000000\n", "BOOT\n", "M5 M3 S1\n",
        "M115 X1\n", "UPDATE NOW\n", "INFO\rUPDATE\n", "\n",
    };
    for (uint32_t i = 0; i < sizeof(rejected) / sizeof(rejected[0]); ++i) {
        clear_output();
        app_send(rejected[i]);
        CHECK(same(output, "ERR UNSUPPORTED\n"));
        CHECK(resets == 1 && off_calls == 3);
        CHECK(erased == 0 && programmed == 0 && booted == 0);
    }
    clear_output();
    for (uint32_t i = 0; i < 300; ++i) application_receive('X');
    app_send("UPDATE\nINFO\n");
    CHECK(same(output, "ERR LINE\n" E3_APPLICATION_ID "\n"));
    CHECK(resets == 1);
    clear_output();
    app_send("UP");
    application_receive(-2);
    app_send("DATE\nINFO\n");
    CHECK(same(output, "ERR UART\nERR LINE\n" E3_APPLICATION_ID "\n"));
    CHECK(resets == 1 && off_calls == 4);
    clear_output();
    application_receive(0);
    app_send("UPDATE\n");
    CHECK(same(output, "ERR LINE\n") && resets == 1);
    CHECK(!illegal_access);
    return 0;
}

int tests_main(void) {
    int result = test_crc_and_header();
    if (result) return result;
    result = test_vectors_and_commit();
    if (result) return result;
    result = test_valid_upload_and_boot();
    if (result) return result;
    result = test_begin_rejections();
    if (result) return result;
    result = test_data_and_end_rejections();
    if (result) return result;
    result = test_input_and_startup();
    if (result) return result;
    result = test_interruption_and_recovery();
    if (result) return result;
    return test_application();
}
