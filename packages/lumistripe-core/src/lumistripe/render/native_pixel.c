#include "native_pixel.h"

#if defined(__aarch64__) && !defined(LUMISTRIPE_DISABLE_RENDER_NEON)
#include <arm_neon.h>

static uint8x16_t scale_channel(uint8x16_t channel, uint8_t correction) {
    const uint8x8_t gain = vdup_n_u8(correction);
    const uint16x8_t low_product = vmull_u8(vget_low_u8(channel), gain);
    const uint16x8_t high_product = vmull_u8(vget_high_u8(channel), gain);
    const uint16x8_t low_rounded = vaddq_u16(
        low_product, vaddq_u16(vshrq_n_u16(low_product, 8), vdupq_n_u16(1)));
    const uint16x8_t high_rounded = vaddq_u16(
        high_product, vaddq_u16(vshrq_n_u16(high_product, 8), vdupq_n_u16(1)));
    const uint8x8_t low_scaled = vmovn_u16(vshrq_n_u16(low_rounded, 8));
    const uint8x8_t high_scaled = vmovn_u16(vshrq_n_u16(high_rounded, 8));
    return vcombine_u8(low_scaled, high_scaled);
}
#endif

void lumistripe_correct_rgba_scalar(const uint8_t *source, uint8_t *destination,
                                    size_t pixel_count,
                                    const uint16_t correction[3]) {
    for (size_t index = 0; index < pixel_count; index++) {
        destination[0] = (uint8_t)((uint32_t)source[0] * correction[0] / 255u);
        destination[1] = (uint8_t)((uint32_t)source[1] * correction[1] / 255u);
        destination[2] = (uint8_t)((uint32_t)source[2] * correction[2] / 255u);
        destination[3] = source[3];
        source += 4;
        destination += 4;
    }
}

void lumistripe_correct_rgba(const uint8_t *source, uint8_t *destination,
                             size_t pixel_count,
                             const uint16_t correction[3]) {
#if defined(__aarch64__) && !defined(LUMISTRIPE_DISABLE_RENDER_NEON)
    size_t index = 0;
    while (pixel_count - index >= 16u) {
        const uint8x16x4_t rgba = vld4q_u8(source);
        uint8x16x4_t corrected;
        corrected.val[0] = scale_channel(rgba.val[0], (uint8_t)correction[0]);
        corrected.val[1] = scale_channel(rgba.val[1], (uint8_t)correction[1]);
        corrected.val[2] = scale_channel(rgba.val[2], (uint8_t)correction[2]);
        corrected.val[3] = rgba.val[3];
        vst4q_u8(destination, corrected);
        source += 64;
        destination += 64;
        index += 16u;
    }
    lumistripe_correct_rgba_scalar(source, destination, pixel_count - index,
                                   correction);
#else
    lumistripe_correct_rgba_scalar(source, destination, pixel_count, correction);
#endif
}
