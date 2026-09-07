#include "image.h"
#include "platform.h"

static uint32_t get_le32(const uint8_t *p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) |
           ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

static void put_le32(uint8_t *p, uint32_t value) {
    for (uint32_t i = 0; i < 4; ++i) p[i] = (uint8_t)(value >> (8 * i));
}

static uint32_t crc_byte(uint32_t crc, uint8_t byte) {
    crc ^= byte;
    for (uint32_t bit = 0; bit < 8; ++bit)
        crc = (crc >> 1) ^ ((crc & 1) ? UINT32_C(0xEDB88320) : 0);
    return crc;
}

uint32_t image_crc32(const uint8_t *data, uint32_t length) {
    uint32_t crc = UINT32_C(0xFFFFFFFF);
    for (uint32_t i = 0; i < length; ++i) crc = crc_byte(crc, data[i]);
    return crc ^ UINT32_C(0xFFFFFFFF);
}

int image_metadata_valid(uint32_t board, uint32_t length) {
    return board == E3_BOARD_ID && length >= E3_PAYLOAD_MINIMUM &&
           length <= E3_PAYLOAD_CAPACITY && !(length & 3);
}

int image_validate_header(const uint8_t *header) {
    if (get_le32(header) != E3_IMAGE_MAGIC ||
        get_le32(header + 4) != E3_IMAGE_FORMAT ||
        !image_metadata_valid(get_le32(header + 8), get_le32(header + 12)) ||
        get_le32(header + 20) != E3_IMAGE_VERSION ||
        get_le32(header + 24) != image_crc32(header + 4, 20)) return 0;
    for (uint32_t i = 28; i < E3_HEADER_SIZE; ++i)
        if (header[i] != 0xFF) return 0;
    return 1;
}

int image_validate_payload(uint32_t length, uint32_t expected_crc) {
    if (!image_metadata_valid(E3_BOARD_ID, length)) return 0;
    uint32_t stack = platform_read_word(E3_PAYLOAD_ADDRESS);
    uint32_t reset = platform_read_word(E3_PAYLOAD_ADDRESS + 4);
    uint32_t target = reset & ~UINT32_C(1);
    if ((stack & 7) || stack <= E3_SRAM_START || stack > E3_SRAM_END ||
        !(reset & 1) || target < E3_PAYLOAD_ADDRESS + E3_VECTOR_SIZE ||
        target >= E3_PAYLOAD_ADDRESS + length) return 0;
    uint32_t crc = UINT32_C(0xFFFFFFFF);
    for (uint32_t i = 0; i < length; i += 4) {
        if (!(i & 1023)) platform_service();
        uint32_t word = platform_read_word(E3_PAYLOAD_ADDRESS + i);
        for (uint32_t j = 0; j < 4; ++j)
            crc = crc_byte(crc, (uint8_t)(word >> (8 * j)));
    }
    return (crc ^ UINT32_C(0xFFFFFFFF)) == expected_crc;
}

int image_validate(void) {
    uint8_t header[E3_HEADER_SIZE];
    for (uint32_t i = 0; i < E3_HEADER_SIZE; i += 4)
        put_le32(header + i, platform_read_word(E3_HEADER_ADDRESS + i));
    return image_validate_header(header) &&
           image_validate_payload(get_le32(header + 12), get_le32(header + 16));
}

int image_commit(uint32_t length, uint32_t crc) {
    /* Commit is permitted only onto an erased header after full payload checks. */
    if (!image_validate_payload(length, crc)) return 0;
    for (uint32_t i = 0; i < E3_HEADER_SIZE; i += 4)
        if (platform_read_word(E3_HEADER_ADDRESS + i) != UINT32_C(0xFFFFFFFF))
            return 0;
    uint8_t header[28];
    put_le32(header, E3_IMAGE_MAGIC);
    put_le32(header + 4, E3_IMAGE_FORMAT);
    put_le32(header + 8, E3_BOARD_ID);
    put_le32(header + 12, length);
    put_le32(header + 16, crc);
    put_le32(header + 20, E3_IMAGE_VERSION);
    put_le32(header + 24, image_crc32(header + 4, 20));
    /* An interruption during metadata writes leaves no valid magic. */
    for (uint32_t i = 4; i < sizeof(header); i += 4) {
        uint32_t word = get_le32(header + i);
        if (!platform_program_word(E3_HEADER_ADDRESS + i, word) ||
            platform_read_word(E3_HEADER_ADDRESS + i) != word) return 0;
    }
    if (!platform_program_word(E3_HEADER_ADDRESS, E3_IMAGE_MAGIC)) return 0;
    return image_validate();
}
