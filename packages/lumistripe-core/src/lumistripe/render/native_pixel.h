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
void lumistripe_apply_alpha_scale(const uint8_t *source, uint8_t *corrected,
                                  uint8_t *destination, size_t pixel_count,
                                  double scale);
void lumistripe_apply_alpha_scale_scalar(const uint8_t *source,
                                         uint8_t *corrected,
                                         uint8_t *destination,
                                         size_t pixel_count, double scale);
void lumistripe_composite_screen_rgba(
    const uint8_t *base, uint8_t *destination,
    const uint8_t *overlay_1, const uint8_t *overlay_2,
    size_t pixel_count, float base_intensity, float strength_1,
    float strength_2);
void lumistripe_composite_screen_rgba_scalar(
    const uint8_t *base, uint8_t *destination,
    const uint8_t *overlay_1, const uint8_t *overlay_2,
    size_t pixel_count, float base_intensity, float strength_1,
    float strength_2);

#endif
