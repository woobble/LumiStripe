#ifndef LUMISTRIPE_RENDER_NATIVE_PIXEL_H
#define LUMISTRIPE_RENDER_NATIVE_PIXEL_H

#include <stddef.h>
#include <stdint.h>

void lumistripe_correct_rgba_scalar(const uint8_t *source, uint8_t *destination,
                                    size_t pixel_count,
                                    const uint16_t correction[3]);
void lumistripe_correct_rgba(const uint8_t *source, uint8_t *destination,
                             size_t pixel_count,
                             const uint16_t correction[3]);

#endif
