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

#if defined(__aarch64__) && !defined(LUMISTRIPE_DISABLE_RENDER_NEON)
static float32x4_t u8x8_to_f32_low(uint8x8_t value) {
    return vcvtq_f32_u32(vmovl_u16(vget_low_u16(vmovl_u8(value))));
}

static float32x4_t u8x8_to_f32_high(uint8x8_t value) {
    return vcvtq_f32_u32(vmovl_u16(vget_high_u16(vmovl_u8(value))));
}

static float32x4_t visible_channel_low(uint8x8_t channel,
                                       float32x4_t normalized_alpha,
                                       float32x4_t reciprocal_255) {
    return vmulq_f32(vmulq_f32(u8x8_to_f32_low(channel), reciprocal_255),
                     normalized_alpha);
}

static float32x4_t visible_channel_high(uint8x8_t channel,
                                        float32x4_t normalized_alpha,
                                        float32x4_t reciprocal_255) {
    return vmulq_f32(vmulq_f32(u8x8_to_f32_high(channel), reciprocal_255),
                     normalized_alpha);
}

static float32x4_t screen_blend4(float32x4_t composed,
                                 float32x4_t overlay) {
    const float32x4_t base_remaining = vsubq_f32(vdupq_n_f32(1.0f), composed);
    const float32x4_t overlay_remaining = vsubq_f32(vdupq_n_f32(1.0f), overlay);
    return vsubq_f32(vdupq_n_f32(1.0f),
                     vmulq_f32(base_remaining, overlay_remaining));
}

static uint8x8_t finish_channel(float32x4_t low, float32x4_t high) {
    const float32x4_t zero = vdupq_n_f32(0.0f);
    const float32x4_t maximum = vdupq_n_f32(255.0f);
    const uint32x4_t low_u32 = vcvtq_u32_f32(vminq_f32(maximum, vmaxq_f32(zero, low)));
    const uint32x4_t high_u32 = vcvtq_u32_f32(vminq_f32(maximum, vmaxq_f32(zero, high)));
    return vmovn_u16(vcombine_u16(vmovn_u32(low_u32), vmovn_u32(high_u32)));
}
#endif

void lumistripe_apply_alpha_scale(const uint8_t *source, uint8_t *corrected,
                                  uint8_t *destination, size_t pixel_count,
                                  double scale) {
    for (size_t index = 0; index < pixel_count; index++) {
        const size_t offset = index * 4u;
        const uint8_t alpha = (uint8_t)((double)source[offset + 3u] * scale);
        destination[offset] = source[offset];
        destination[offset + 1u] = source[offset + 1u];
        destination[offset + 2u] = source[offset + 2u];
        destination[offset + 3u] = alpha;
        corrected[offset + 3u] = alpha;
    }
}

static float visible_channel_scalar(const uint8_t *pixel, size_t channel) {
    const float normalized = (float)pixel[channel] / 255.0f;
    const float alpha = (float)pixel[3u] / 255.0f;
    return normalized * alpha;
}

static float screen_blend_scalar(float composed, float overlay) {
    const float base_remaining = 1.0f - composed;
    const float overlay_remaining = 1.0f - overlay;
    volatile float remaining = base_remaining * overlay_remaining;
    return 1.0f - remaining;
}

void lumistripe_composite_screen_rgba_scalar(
    const uint8_t *base, uint8_t *destination,
    const uint8_t *overlay_1, const uint8_t *overlay_2,
    size_t pixel_count, float base_intensity, float strength_1,
    float strength_2) {
    for (size_t index = 0; index < pixel_count; index++) {
        const uint8_t *base_pixel = base + index * 4u;
        const uint8_t *first_pixel = overlay_1 == NULL ? NULL : overlay_1 + index * 4u;
        const uint8_t *second_pixel = overlay_2 == NULL ? NULL : overlay_2 + index * 4u;
        for (size_t channel = 0; channel < 3u; channel++) {
            float composed = visible_channel_scalar(base_pixel, channel) * base_intensity;
            if (first_pixel != NULL && strength_1 > 0.0f) {
                const float visible = visible_channel_scalar(first_pixel, channel) * strength_1;
                composed = screen_blend_scalar(composed, visible);
            }
            if (second_pixel != NULL && strength_2 > 0.0f) {
                const float visible = visible_channel_scalar(second_pixel, channel) * strength_2;
                composed = screen_blend_scalar(composed, visible);
            }
            const float scaled = composed * 255.0f;
            destination[index * 4u + channel] = !(scaled > 0.0f) ? 0u :
                scaled >= 255.0f ? 255u : (uint8_t)scaled;
        }
        destination[index * 4u + 3u] = 255u;
    }
}

void lumistripe_composite_screen_rgba(
    const uint8_t *base, uint8_t *destination,
    const uint8_t *overlay_1, const uint8_t *overlay_2,
    size_t pixel_count, float base_intensity, float strength_1,
    float strength_2) {
#if defined(__aarch64__) && !defined(LUMISTRIPE_DISABLE_RENDER_NEON)
    /* Large values can overflow the ordered SCREEN intermediates. Preserve the
       scalar handling of those uncommon but supported finite inputs. */
    if (base_intensity < -4.0f || base_intensity > 4.0f ||
        strength_1 > 4.0f || strength_2 > 4.0f) {
        lumistripe_composite_screen_rgba_scalar(
            base, destination, overlay_1, overlay_2, pixel_count,
            base_intensity, strength_1, strength_2);
        return;
    }
    size_t index = 0;
    const float32x4_t reciprocal_255 = vdupq_n_f32(1.0f / 255.0f);
    const float32x4_t output_scale = vdupq_n_f32(255.0f);
    const float32x4_t base_gain = vdupq_n_f32(base_intensity);
    const float32x4_t first_gain = vdupq_n_f32(strength_1);
    const float32x4_t second_gain = vdupq_n_f32(strength_2);
    while (pixel_count - index >= 8u) {
        const uint8x8x4_t base_rgba = vld4_u8(base + index * 4u);
        uint8x8x4_t first_rgba;
        uint8x8x4_t second_rgba;
        if (overlay_1 != NULL)
            first_rgba = vld4_u8(overlay_1 + index * 4u);
        if (overlay_2 != NULL)
            second_rgba = vld4_u8(overlay_2 + index * 4u);
        const float32x4_t base_alpha_low = vmulq_f32(
            u8x8_to_f32_low(base_rgba.val[3]), reciprocal_255);
        const float32x4_t base_alpha_high = vmulq_f32(
            u8x8_to_f32_high(base_rgba.val[3]), reciprocal_255);
        float32x4_t first_alpha_low;
        float32x4_t first_alpha_high;
        float32x4_t second_alpha_low;
        float32x4_t second_alpha_high;
        if (overlay_1 != NULL) {
            first_alpha_low = vmulq_f32(
                u8x8_to_f32_low(first_rgba.val[3]), reciprocal_255);
            first_alpha_high = vmulq_f32(
                u8x8_to_f32_high(first_rgba.val[3]), reciprocal_255);
        }
        if (overlay_2 != NULL) {
            second_alpha_low = vmulq_f32(
                u8x8_to_f32_low(second_rgba.val[3]), reciprocal_255);
            second_alpha_high = vmulq_f32(
                u8x8_to_f32_high(second_rgba.val[3]), reciprocal_255);
        }
        uint8x8x4_t output;
        for (size_t channel = 0; channel < 3u; channel++) {
            float32x4_t low = vmulq_f32(
                visible_channel_low(base_rgba.val[channel], base_alpha_low,
                                    reciprocal_255), base_gain);
            float32x4_t high = vmulq_f32(
                visible_channel_high(base_rgba.val[channel], base_alpha_high,
                                     reciprocal_255), base_gain);
            if (overlay_1 != NULL && strength_1 > 0.0f) {
                const float32x4_t first_low = vmulq_f32(
                    visible_channel_low(first_rgba.val[channel], first_alpha_low,
                                        reciprocal_255), first_gain);
                const float32x4_t first_high = vmulq_f32(
                    visible_channel_high(first_rgba.val[channel], first_alpha_high,
                                         reciprocal_255), first_gain);
                low = screen_blend4(low, first_low);
                high = screen_blend4(high, first_high);
            }
            if (overlay_2 != NULL && strength_2 > 0.0f) {
                const float32x4_t second_low = vmulq_f32(
                    visible_channel_low(second_rgba.val[channel], second_alpha_low,
                                        reciprocal_255), second_gain);
                const float32x4_t second_high = vmulq_f32(
                    visible_channel_high(second_rgba.val[channel], second_alpha_high,
                                         reciprocal_255), second_gain);
                low = screen_blend4(low, second_low);
                high = screen_blend4(high, second_high);
            }
            output.val[channel] = finish_channel(vmulq_f32(low, output_scale),
                                                vmulq_f32(high, output_scale));
        }
        output.val[3] = vdup_n_u8(255u);
        vst4_u8(destination + index * 4u, output);
        index += 8u;
    }
    lumistripe_composite_screen_rgba_scalar(
        base + index * 4u, destination + index * 4u,
        overlay_1 == NULL ? NULL : overlay_1 + index * 4u,
        overlay_2 == NULL ? NULL : overlay_2 + index * 4u,
        pixel_count - index, base_intensity, strength_1, strength_2);
#else
    lumistripe_composite_screen_rgba_scalar(
        base, destination, overlay_1, overlay_2, pixel_count,
        base_intensity, strength_1, strength_2);
#endif
}
