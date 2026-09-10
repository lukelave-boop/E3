#ifndef E3_AUX_IMAGE_H
#define E3_AUX_IMAGE_H

#include <stdint.h>

#define E3_IMAGE_MAGIC UINT32_C(0x55413345)
#define E3_IMAGE_FORMAT UINT32_C(1)
#define E3_BOARD_ID UINT32_C(0x0401C013)
#define E3_IMAGE_VERSION UINT32_C(1)
#define E3_HEADER_ADDRESS UINT32_C(0x08020000)
#define E3_HEADER_SIZE UINT32_C(512)
#define E3_PAYLOAD_ADDRESS UINT32_C(0x08020200)
#define E3_PAYLOAD_CAPACITY UINT32_C(0x0001FE00)
#define E3_VECTOR_SIZE UINT32_C(404)
#define E3_PAYLOAD_MINIMUM UINT32_C(408)
#define E3_SRAM_START UINT32_C(0x20000000)
#define E3_SRAM_END UINT32_C(0x20010000)

uint32_t image_crc32(const uint8_t *data, uint32_t length);
int image_metadata_valid(uint32_t board, uint32_t length);
int image_validate_header(const uint8_t *header);
int image_validate_payload(uint32_t length, uint32_t crc);
int image_validate(void);
int image_commit(uint32_t length, uint32_t crc);

#endif
