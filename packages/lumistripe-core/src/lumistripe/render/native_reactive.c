#include "native_common.h"

#if defined(__aarch64__) && !defined(LUMISTRIPE_DISABLE_RENDER_NEON)
#include <arm_neon.h>
#define LUMISTRIPE_REACTIVE_NEON 1

static void store_alpha_pair(uint8_t *pixels, npy_intp index,
                             const double alpha[2], double brightness,
                             int clamp_alpha) {
    float64x2_t value = vmulq_f64(vld1q_f64(alpha), vdupq_n_f64(brightness));
    if (clamp_alpha)
        value = vminq_f64(value, vdupq_n_f64(1.0));
    value = vmulq_f64(value, vdupq_n_f64(255.0));
    uint64_t converted[2];
    vst1q_u64(converted, vcvtq_u64_f64(value));
    pixels[(size_t)(index - 1) * 4u + 3u] = (uint8_t)converted[0];
    pixels[(size_t)index * 4u + 3u] = (uint8_t)converted[1];
}
#endif

PyObject *render_shockwave_into(PyObject *module, PyObject *args) {
    (void)module;
    PyArrayObject *destination;
    PyObject *bands_object;
    double radius, width, accent, mid, brightness;
    int hue, audio_mode, palette;
    unsigned int custom_rgb;
    if (!PyArg_ParseTuple(args, "O!dddiddiOiI", &PyArray_Type, &destination,
                          &radius, &width, &accent, &hue, &mid, &brightness,
                          &audio_mode, &bands_object, &palette, &custom_rgb))
        return NULL;
    if (validate_rgba(destination, "destination", 1) < 0)
        return NULL;
    if (!isfinite(radius) || !isfinite(width) || width <= 0.0 ||
        !isfinite(accent) || accent < 0.0 || accent > 1.0 || hue < 0 || hue > 255 ||
        !isfinite(mid) || mid < 0.0 || mid > 1.0 ||
        !isfinite(brightness) || brightness < 0.0 || brightness > 1.0 ||
        (audio_mode != 0 && audio_mode != 1) || palette < 0 || palette > 3 ||
        custom_rgb > 0xFFFFFFu) {
        PyErr_SetString(PyExc_ValueError, "invalid Shockwave render parameters");
        return NULL;
    }
    double bands[8] = {0.0};
    if (audio_mode) {
        PyObject *sequence = PySequence_Fast(bands_object, "bands must be a sequence");
        if (sequence == NULL)
            return NULL;
        if (PySequence_Fast_GET_SIZE(sequence) != 8) {
            Py_DECREF(sequence);
            PyErr_SetString(PyExc_ValueError, "bands must contain exactly eight values");
            return NULL;
        }
        for (int band = 0; band < 8; band++) {
            bands[band] = PyFloat_AsDouble(PySequence_Fast_GET_ITEM(sequence, band));
            if (PyErr_Occurred() || !isfinite(bands[band]) ||
                bands[band] < 0.0 || bands[band] > 1.0) {
                Py_DECREF(sequence);
                if (!PyErr_Occurred())
                    PyErr_SetString(PyExc_ValueError,
                                    "bands must be normalized finite values");
                return NULL;
            }
        }
        Py_DECREF(sequence);
    }
    const npy_intp count = PyArray_DIM(destination, 0);
    const double center = (double)(count > 1 ? count - 1 : 0) * 0.5;
    uint8_t *pixels = (uint8_t *)PyArray_DATA(destination);
    for (npy_intp index = 0; index < count; index++) {
        const double distance = count <= 1 ? 0.0 : fabs((double)index - center);
        const double ring = fmax(0.0, 1.0 - fabs(distance - radius) / width);
        const double ambient = fmax(1.0 - distance / fmax(center, 1.0), 0.0) * 0.12;
        double alpha = ambient + ring * 0.88;
        int saturation = 100;
        int lightness = 58;
        if (audio_mode) {
            double local;
            if (count <= 1) {
                local = bands[0];
            } else {
                const double band_position = (double)index * 8.0 / (double)(count - 1);
                const int band_center = (int)band_position;
                const int start = band_center > 0 ? band_center - 1 : 0;
                const int end = band_center + 2 < 8 ? band_center + 2 : 8;
                local = 0.0;
                for (int band = start; band < end; band++)
                    local += bands[band];
                local /= (double)(end - start);
            }
            alpha += accent * 0.05;
            saturation = 95;
            lightness = (int)fmin(52.0 + mid * 10.0 + local * 8.0 + accent * 4.0, 68.0);
        }
        alpha = fmin(alpha, 1.0);
        uint8_t red, green, blue;
        if (palette == 3) {
            red = (uint8_t)((custom_rgb >> 16u) & 0xFFu);
            green = (uint8_t)((custom_rgb >> 8u) & 0xFFu);
            blue = (uint8_t)(custom_rgb & 0xFFu);
        } else {
            hsl_to_rgb((double)hue, (double)saturation, (double)lightness,
                       &red, &green, &blue);
            if (palette == 1 || palette == 2)
                map_palette(&red, &green, &blue, palette);
        }
        const size_t offset = (size_t)index * 4u;
        pixels[offset] = red;
        pixels[offset + 1u] = green;
        pixels[offset + 2u] = blue;
        pixels[offset + 3u] = (uint8_t)(alpha * brightness * 255.0);
    }
    Py_RETURN_NONE;
}

static PyObject *render_aurora_impl(PyObject *module, PyObject *args,
                                    int use_neon) {
    (void)module;
    (void)use_neon;
    PyArrayObject *destination;
    PyObject *bands_object;
    Py_ssize_t frame;
    int audio_mode, hue_offset, palette;
    double frequency, speed, low, mid, high, beat_glow, brightness;
    unsigned int custom_rgb;
    if (!PyArg_ParseTuple(args, "O!niO" "ddddddd" "iiI", &PyArray_Type, &destination,
                          &frame, &audio_mode, &bands_object, &frequency, &speed,
                          &low, &mid, &high, &beat_glow, &brightness, &hue_offset,
                          &palette, &custom_rgb))
        return NULL;
    if (validate_rgba(destination, "destination", 1) < 0)
        return NULL;
    if ((audio_mode != 0 && audio_mode != 1) || !isfinite(frequency) || frequency <= 0.0 ||
        !isfinite(speed) || !isfinite(low) || low < 0.0 || low > 1.0 ||
        !isfinite(mid) || mid < 0.0 || mid > 1.0 ||
        !isfinite(high) || high < 0.0 || high > 1.0 ||
        !isfinite(beat_glow) || beat_glow < 0.0 || beat_glow > 1.0 ||
        !isfinite(brightness) || brightness < 0.0 || brightness > 1.0 ||
        hue_offset < 0 || hue_offset > 255 || palette < 0 || palette > 3 ||
        custom_rgb > 0xFFFFFFu) {
        PyErr_SetString(PyExc_ValueError, "invalid Aurora render parameters");
        return NULL;
    }
    double bands[8] = {0.0};
    if (audio_mode) {
        PyObject *sequence = PySequence_Fast(bands_object, "bands must be a sequence");
        if (sequence == NULL)
            return NULL;
        if (PySequence_Fast_GET_SIZE(sequence) != 8) {
            Py_DECREF(sequence);
            PyErr_SetString(PyExc_ValueError, "bands must contain exactly eight values");
            return NULL;
        }
        for (int band = 0; band < 8; band++) {
            bands[band] = PyFloat_AsDouble(PySequence_Fast_GET_ITEM(sequence, band));
            if (PyErr_Occurred() || !isfinite(bands[band]) ||
                bands[band] < 0.0 || bands[band] > 1.0) {
                Py_DECREF(sequence);
                if (!PyErr_Occurred())
                    PyErr_SetString(PyExc_ValueError,
                                    "bands must be normalized finite values");
                return NULL;
            }
        }
        Py_DECREF(sequence);
    }
    const npy_intp count = PyArray_DIM(destination, 0);
    const double max_position = count > 1 ? 1.0 : 0.0;
    const double static_phase_start = (double)frame * 0.038;
    const double static_phase_end = static_phase_start + max_position * 7.5 * frequency;
    const double audio_low_start = (double)frame * speed + low * 4.0;
    const double audio_low_end =
        (double)frame * speed + max_position * 5.8 * frequency + low * 4.0;
    const double audio_mid_start = (double)frame * (speed * 0.9) + mid * 5.0;
    const double audio_mid_end =
        (double)frame * (speed * 0.9) + max_position * 8.5 * frequency + mid * 5.0;
    const double audio_high_start = (double)frame * (speed * 1.7) + high * 7.0;
    const double audio_high_end =
        (double)frame * (speed * 1.7) - max_position * 11.5 * frequency + high * 7.0;
    if (!isfinite(static_phase_start) || !isfinite(static_phase_end) ||
        !isfinite(audio_low_start) || !isfinite(audio_low_end) ||
        !isfinite(audio_mid_start) || !isfinite(audio_mid_end) ||
        !isfinite(audio_high_start) || !isfinite(audio_high_end)) {
        PyErr_SetString(PyExc_ValueError, "Aurora phase values exceed the finite range");
        return NULL;
    }
    uint8_t *pixels = (uint8_t *)PyArray_DATA(destination);
#if defined(LUMISTRIPE_REACTIVE_NEON)
    double alpha_pair[2];
#endif
    for (npy_intp index = 0; index < count; index++) {
        const double position = count <= 1 ? 0.0 : (double)index / (double)(count - 1);
        double alpha, hue;
        int saturation, lightness;
        if (!audio_mode) {
            const double phase = (double)frame * 0.038 + position * 7.5 * frequency;
            const double curtain = (sin(phase) * 0.5 + 0.5) * 0.58 +
                (sin(phase * 0.55 + 1.4) * 0.5 + 0.5) * 0.22;
            alpha = fmin(0.12 + curtain, 1.0);
            hue = (double)(int)(132.0 + cos(phase * 0.75) * 24.0 + position * 10.0);
            saturation = 100;
            lightness = 56;
        } else {
            double local;
            if (count <= 1) {
                local = bands[0];
            } else {
                const double band_position = (double)index * 8.0 / (double)(count - 1);
                const int band_center = (int)band_position;
                const int start = band_center > 0 ? band_center - 1 : 0;
                const int end = band_center + 2 < 8 ? band_center + 2 : 8;
                local = 0.0;
                for (int band = start; band < end; band++)
                    local += bands[band];
                local /= (double)(end - start);
            }
            const double low_wave = sin((double)frame * speed + position * 5.8 * frequency + low * 4.0);
            const double mid_wave = sin((double)frame * (speed * 0.9) + position * 8.5 * frequency + mid * 5.0);
            const double high_wave = cos((double)frame * (speed * 1.7) - position * 11.5 * frequency + high * 7.0);
            const double curtain = (low_wave * 0.38 + mid_wave * 0.34 + high_wave * 0.28) * 0.5 + 0.5;
            alpha = fmin(0.12 + curtain * 0.72 + beat_glow * 0.2, 1.0);
            hue = (double)((hue_offset + 120 + (int)(position * 34.0) +
                            (int)(mid * 16.0) + (int)(local * 10.0)) % 256);
            saturation = 90;
            lightness = (int)fmin(50.0 + high * 12.0 + local * 8.0 + beat_glow * 8.0, 68.0);
        }
        uint8_t red, green, blue;
        if (palette == 3) {
            red = (uint8_t)((custom_rgb >> 16u) & 0xFFu);
            green = (uint8_t)((custom_rgb >> 8u) & 0xFFu);
            blue = (uint8_t)(custom_rgb & 0xFFu);
        } else {
            hsl_to_rgb(hue, (double)saturation, (double)lightness, &red, &green, &blue);
            if (palette == 1 || palette == 2)
                map_palette(&red, &green, &blue, palette);
        }
        const size_t offset = (size_t)index * 4u;
        pixels[offset] = red;
        pixels[offset + 1u] = green;
        pixels[offset + 2u] = blue;
#if defined(LUMISTRIPE_REACTIVE_NEON)
        if (use_neon) {
            alpha_pair[index & 1] = alpha;
            if (index & 1)
                store_alpha_pair(pixels, index, alpha_pair, brightness, 0);
        } else {
            pixels[offset + 3u] = (uint8_t)(alpha * brightness * 255.0);
        }
#else
        pixels[offset + 3u] = (uint8_t)(alpha * brightness * 255.0);
#endif
    }
#if defined(LUMISTRIPE_REACTIVE_NEON)
    if (use_neon && (count & 1)) {
        const size_t offset = (size_t)(count - 1) * 4u;
        pixels[offset + 3u] = (uint8_t)(alpha_pair[0] * brightness * 255.0);
    }
#endif
    Py_RETURN_NONE;
}

PyObject *render_spectrum_flash_into(PyObject *module, PyObject *args) {
    (void)module;
    PyArrayObject *destination;
    PyObject *bands_object;
    double width, hue_phase, flash, brightness;
    int palette;
    unsigned int custom_rgb;
    if (!PyArg_ParseTuple(args, "O!Oddddii", &PyArray_Type, &destination,
                          &bands_object, &width, &hue_phase, &flash,
                          &brightness, &palette, &custom_rgb))
        return NULL;
    if (validate_rgba(destination, "destination", 1) < 0)
        return NULL;
    PyObject *bands = PySequence_Fast(bands_object, "bands must be a sequence");
    if (bands == NULL)
        return NULL;
    if (PySequence_Fast_GET_SIZE(bands) != 8) {
        Py_DECREF(bands);
        PyErr_SetString(PyExc_ValueError, "bands must contain exactly eight values");
        return NULL;
    }
    const npy_intp count = PyArray_DIM(destination, 0);
    if (!isfinite(width) || !isfinite(hue_phase) ||
        fabs(hue_phase / 8.0) > (double)INT_MAX || !isfinite(flash) ||
        !isfinite(brightness) || width < 0.0 || flash < 0.0 || flash > 1.0 ||
        brightness < 0.0 ||
        brightness > 1.0 || palette < 0 || palette > 3 ||
        custom_rgb > 0xFFFFFFu) {
        Py_DECREF(bands);
        PyErr_SetString(PyExc_ValueError, "invalid SpectrumFlash render parameters");
        return NULL;
    }
    double band_values[8];
    for (Py_ssize_t i = 0; i < 8; i++) {
        band_values[i] = PyFloat_AsDouble(PySequence_Fast_GET_ITEM(bands, i));
        if (PyErr_Occurred() || !isfinite(band_values[i]) ||
            band_values[i] < 0.0 || band_values[i] > 1.0) {
            Py_DECREF(bands);
            if (!PyErr_Occurred())
                PyErr_SetString(PyExc_ValueError, "bands must be finite values between zero and one");
            return NULL;
        }
    }
    Py_DECREF(bands);

    uint8_t *output = (uint8_t *)PyArray_DATA(destination);
    const double blend = width;
    volatile double flash_scaled = flash * 0.9;
    const double flash_alpha = fmin(1.0, 0.1 + flash_scaled);
    for (npy_intp index = 0; index < count; index++) {
        const double ratio = count <= 1 ? 0.0 : (double)index / (double)(count - 1);
        const int band_index = (int)(ratio * 8.0) < 8 ? (int)(ratio * 8.0) : 7;
        const double neighbor = (band_values[band_index > 0 ? band_index - 1 : 0] +
                                 band_values[band_index < 7 ? band_index + 1 : 7]) * 0.5;
        volatile double primary_band = band_values[band_index] * (1.0 - blend);
        volatile double neighbor_band = neighbor * blend;
        const double band = primary_band + neighbor_band;
        volatile double scaled_band = band * 0.72;
        const double alpha = fmin(1.0, fmax(0.0, 0.18 + scaled_band));
        double hue = fmod(hue_phase + ratio * 300.0, 360.0);
        double saturation = 100.0;
        double lightness = 56.0;
        double pixel_alpha = alpha;
        if (flash > 0.0) {
            static const int warm_hues[4] = {0, 20, 38, 320};
            const int hue_seed = (int)(hue_phase / 8.0);
            int seed_mod = (hue_seed % 4 + (int)(index % 4)) % 4;
            if (seed_mod < 0)
                seed_mod += 4;
            hue = warm_hues[seed_mod];
            lightness = 60.0;
            pixel_alpha = flash_alpha;
        }
        uint8_t red, green, blue;
        if (palette == 3) {
            red = (uint8_t)((custom_rgb >> 16) & 0xFFu);
            green = (uint8_t)((custom_rgb >> 8) & 0xFFu);
            blue = (uint8_t)(custom_rgb & 0xFFu);
        } else {
            hsl_to_rgb(hue, saturation, lightness, &red, &green, &blue);
            map_palette(&red, &green, &blue, palette);
        }
        const size_t offset = (size_t)index * 4u;
        output[offset] = red;
        output[offset + 1u] = green;
        output[offset + 2u] = blue;
        volatile double brightness_scaled_alpha = pixel_alpha * brightness;
        volatile double alpha_byte = brightness_scaled_alpha * 255.0;
        output[offset + 3u] = (uint8_t)alpha_byte;
    }
    Py_RETURN_NONE;
}

PyObject *render_pulse_into(PyObject *module, PyObject *args) {
    (void)module;
    PyArrayObject *destination;
    double phase, hue, alpha, brightness;
    int palette;
    unsigned int custom_rgb;
    if (!PyArg_ParseTuple(args, "O!ddddiI", &PyArray_Type, &destination,
                          &phase, &hue, &alpha, &brightness, &palette,
                          &custom_rgb))
        return NULL;
    if (validate_rgba(destination, "destination", 1) < 0)
        return NULL;
    if (!isfinite(phase) || !isfinite(hue) || !isfinite(alpha) ||
        alpha < 0.0 || alpha > 1.0 || !isfinite(brightness) ||
        brightness < 0.0 || brightness > 1.0 || palette < 0 || palette > 3 ||
        custom_rgb > 0xFFFFFFu) {
        PyErr_SetString(PyExc_ValueError, "invalid Pulse render parameters");
        return NULL;
    }
    static const int neon_hues[7] = {300, 160, 52, 206, 12, 268, 132};
    static const int fire_hues[5] = {0, 10, 22, 35, 48};
    const npy_intp count = PyArray_DIM(destination, 0);
    const double wrapped_phase = fmod(phase, 1.0);
    uint8_t *pixels = (uint8_t *)PyArray_DATA(destination);
    for (npy_intp index = 0; index < count; index++) {
        const double spatial = cos(wrapped_phase * 6.283185307179586 +
                                   (double)index * 0.05) * 0.5 + 0.5;
        const int lightness = 46 + (int)(spatial * 5.0);
        uint8_t red, green, blue;
        if (palette == 3) {
            red = (uint8_t)((custom_rgb >> 16u) & 0xFFu);
            green = (uint8_t)((custom_rgb >> 8u) & 0xFFu);
            blue = (uint8_t)(custom_rgb & 0xFFu);
        } else {
            double selected_hue = fmod(hue, 360.0);
            if (palette == 1)
                selected_hue = (double)neon_hues[truncated_modulo_int(hue / 45.0, 7)];
            else if (palette == 2)
                selected_hue = (double)fire_hues[truncated_modulo_int(hue / 55.0, 5)];
            hsl_to_rgb(selected_hue, 82.0, (double)lightness,
                       &red, &green, &blue);
        }
        const size_t offset = (size_t)index * 4u;
        pixels[offset] = red;
        pixels[offset + 1u] = green;
        pixels[offset + 2u] = blue;
        pixels[offset + 3u] = (uint8_t)(alpha * brightness * 255.0);
    }
    Py_RETURN_NONE;
}

PyObject *render_dance_floor_into(PyObject *module, PyObject *args) {
    (void)module;
    PyArrayObject *destination;
    int frame, segment_width, audio_mode, palette;
    double frequency, hue_shift, mid, high, drive, beat_boost, brightness;
    unsigned int custom_rgb;
    if (!PyArg_ParseTuple(args, "O!iididdddddiI", &PyArray_Type, &destination,
                          &frame, &segment_width, &frequency, &audio_mode,
                          &hue_shift, &mid, &high, &drive, &beat_boost,
                          &brightness, &palette, &custom_rgb))
        return NULL;
    if (validate_rgba(destination, "destination", 1) < 0)
        return NULL;
    if (segment_width < 1 || !isfinite(frequency) ||
        (audio_mode != 0 && audio_mode != 1) || !isfinite(hue_shift) ||
        !isfinite(mid) || mid < 0.0 || mid > 1.0 ||
        !isfinite(high) || high < 0.0 || high > 1.0 ||
        !isfinite(drive) || drive < 0.0 || drive > 1.0 ||
        !isfinite(beat_boost) || beat_boost < 0.0 || beat_boost > 1.0 ||
        !isfinite(brightness) || brightness < 0.0 ||
        brightness > 1.0 || palette < 0 || palette > 3 ||
        custom_rgb > 0xFFFFFFu) {
        PyErr_SetString(PyExc_ValueError, "invalid DanceFloor render parameters");
        return NULL;
    }
    const npy_intp count = PyArray_DIM(destination, 0);
    const npy_intp width = (npy_intp)segment_width;
    const npy_intp segments = count > 0
        ? count / width + (count % width != 0)
        : 1;
    const double spatial_rate = 0.4 + frequency / 50.0;
    const double max_spatial_phase = (double)(segments - 1) * spatial_rate;
    const double frame_phase = (double)frame * 0.08;
    if (!isfinite(spatial_rate) || !isfinite(max_spatial_phase) ||
        !isfinite(frame_phase) ||
        fabs(frame_phase) > DBL_MAX - fabs(max_spatial_phase)) {
        PyErr_SetString(PyExc_ValueError, "DanceFloor phase exceeds the finite range");
        return NULL;
    }
    const int hue_shift_mod = truncated_modulo_int(hue_shift, 256);
    const int frame_mod = (int)positive_modulo_ssize((Py_ssize_t)frame, 128);
    uint8_t *pixels = (uint8_t *)PyArray_DATA(destination);
    for (npy_intp index = 0; index < count; index++) {
        const npy_intp segment = index / width;
        const int segment_hue = (int)((segment % 256) * 40 % 256);
        int hue, lightness;
        double alpha = 1.0;
        if (audio_mode) {
            hue = (segment_hue + hue_shift_mod) % 256;
            const double phase = (double)segment * spatial_rate + frame_phase;
            const double breathing = (sin(phase) * 0.5 + 0.5);
            lightness = (int)(30.0 + mid * 20.0 +
                              (double)segment / (double)segments * high * 25.0 +
                              beat_boost * 25.0 + breathing * 4.0);
            alpha = fmin(0.6 + drive * 0.4 + beat_boost * 0.3, 1.0);
        } else {
            hue = (segment_hue + frame_mod * 2) % 256;
            const double phase = (double)segment * spatial_rate + frame_phase;
            lightness = 40 + (int)((1.0 + sin(phase)) * 15.0);
        }
        uint8_t red, green, blue;
        palette_hsl_to_rgb((double)hue, 100.0, (double)lightness,
                           palette, custom_rgb, &red, &green, &blue);
        const size_t offset = (size_t)index * 4u;
        pixels[offset] = red;
        pixels[offset + 1u] = green;
        pixels[offset + 2u] = blue;
        pixels[offset + 3u] = (uint8_t)(alpha * brightness * 255.0);
    }
    Py_RETURN_NONE;
}

PyObject *render_club_flash_into(PyObject *module, PyObject *args) {
    (void)module;
    PyArrayObject *destination;
    long long hue_seed;
    int segment_start, segment_width, whole_strip, palette;
    double flash, brightness;
    unsigned int custom_rgb;
    if (!PyArg_ParseTuple(args, "O!LiiiddiI", &PyArray_Type, &destination,
                          &hue_seed, &segment_start, &segment_width,
                          &whole_strip, &flash, &brightness, &palette,
                          &custom_rgb))
        return NULL;
    if (validate_rgba(destination, "destination", 1) < 0)
        return NULL;
    if (segment_width < 0 || (whole_strip != 0 && whole_strip != 1) ||
        !isfinite(flash) || flash < 0.0 || flash > 1.0 ||
        !isfinite(brightness) || brightness < 0.0 || brightness > 1.0 ||
        palette < 0 || palette > 3 || custom_rgb > 0xFFFFFFu) {
        PyErr_SetString(PyExc_ValueError, "invalid ClubFlash render parameters");
        return NULL;
    }
    static const int club_hues[7] = {330, 18, 48, 126, 190, 224, 286};
    static const int warm_hues[4] = {0, 20, 38, 320};
    const npy_intp count = PyArray_DIM(destination, 0);
    if (!whole_strip && count > 0 &&
        segment_width > (count > 3 ? count : 3)) {
        PyErr_SetString(PyExc_ValueError,
                        "ClubFlash segment is wider than its supported range");
        return NULL;
    }
    uint8_t *pixels = (uint8_t *)PyArray_DATA(destination);
    for (npy_intp index = 0; index < count; index++) {
        const size_t offset = (size_t)index * 4u;
        pixels[offset] = pixels[offset + 1u] = pixels[offset + 2u] = 0u;
        pixels[offset + 3u] = 255u;
    }
    if (flash <= 0.01 || count == 0)
        Py_RETURN_NONE;
    if (whole_strip) {
        const double alpha = fmin(1.0, 0.2 + flash * 0.8);
        const int hue = warm_hues[positive_modulo_i64(hue_seed, 4)];
        uint8_t red, green, blue;
        palette_hsl_to_rgb((double)hue, 100.0, 60.0, palette,
                           custom_rgb, &red, &green, &blue);
        for (npy_intp index = 0; index < count; index++) {
            const size_t offset = (size_t)index * 4u;
            pixels[offset] = red;
            pixels[offset + 1u] = green;
            pixels[offset + 2u] = blue;
            pixels[offset + 3u] = (uint8_t)(alpha * brightness * 255.0);
        }
        Py_RETURN_NONE;
    }
    const double base_alpha = fmin(1.0, 0.12 + flash * 0.88);
    const int base_hue = club_hues[positive_modulo_i64(hue_seed, 7)];
    uint8_t base_red, base_green, base_blue;
    palette_hsl_to_rgb((double)base_hue, 100.0, 58.0, palette,
                       custom_rgb, &base_red, &base_green, &base_blue);
    const int mirror_enabled = flash > 0.6 && count > 6;
    const double mirror_alpha = fmin(1.0, 0.1 + flash * 0.7);
    npy_intp index = positive_modulo_ssize((Py_ssize_t)segment_start, count);
    for (int offset = 0; offset < segment_width; offset++) {
        const size_t pixel_offset = (size_t)index * 4u;
        pixels[pixel_offset] = base_red;
        pixels[pixel_offset + 1u] = base_green;
        pixels[pixel_offset + 2u] = base_blue;
        pixels[pixel_offset + 3u] = (uint8_t)(base_alpha * brightness * 255.0);
        if (mirror_enabled) {
            const npy_intp mirror = (count - 1 - index) > 0 ? count - 1 - index : 0;
            const int warm_index =
                (int)((positive_modulo_i64(hue_seed, 4) + offset % 4) % 4);
            const int warm_hue = warm_hues[warm_index];
            uint8_t red, green, blue;
            palette_hsl_to_rgb((double)warm_hue, 100.0, 60.0,
                               palette, custom_rgb, &red, &green, &blue);
            const size_t mirror_offset = (size_t)mirror * 4u;
            pixels[mirror_offset] = red;
            pixels[mirror_offset + 1u] = green;
            pixels[mirror_offset + 2u] = blue;
            pixels[mirror_offset + 3u] = (uint8_t)(mirror_alpha * brightness * 255.0);
        }
        index = index == count - 1 ? 0 : index + 1;
    }
    Py_RETURN_NONE;
}

PyObject *render_beat_wave_into(PyObject *module, PyObject *args) {
    (void)module;
    PyArrayObject *destination, *waves;
    int wave_count, hue, palette;
    double width, glow, brightness;
    unsigned int custom_rgb;
    if (!PyArg_ParseTuple(args, "O!O!iidddiI", &PyArray_Type, &destination,
                          &PyArray_Type, &waves, &wave_count, &hue, &width,
                          &glow, &brightness, &palette, &custom_rgb))
        return NULL;
    if (validate_rgba(destination, "destination", 1) < 0)
        return NULL;
    const npy_intp wave_shape[2] = {8, 2};
    if (validate_array(waves, NPY_FLOAT64, 2, wave_shape, 0, -INFINITY,
                       INFINITY, "waves") < 0 ||
        reject_any_overlap(destination, waves, "destination", "waves") < 0) {
        return NULL;
    }
    if (wave_count < 0 || wave_count > 8 || !isfinite(width) || width < 1.0 ||
        !isfinite(glow) || glow < 0.0 || glow > 1.0 || !isfinite(brightness) ||
        brightness < 0.0 || brightness > 1.0 || palette < 0 || palette > 3 ||
        custom_rgb > 0xFFFFFFu) {
        PyErr_SetString(PyExc_ValueError, "invalid BeatWave render parameters");
        return NULL;
    }
    const double *wave_data = (const double *)PyArray_DATA(waves);
    const npy_intp length = PyArray_DIM(destination, 0);
    uint8_t *pixels = (uint8_t *)PyArray_DATA(destination);
    for (npy_intp index = 0; index < length; index++) {
        double combined = glow;
        for (int wave = 0; wave < wave_count; wave++) {
            const double pos = wave_data[wave * 2];
            const double strength = wave_data[wave * 2 + 1];
            const double distance = fabs((double)index - pos);
            if (distance < width) {
                const double wave_alpha = strength * (1.0 - distance / width);
                combined = fmin(combined + wave_alpha, 1.0);
            }
        }
        const size_t offset = (size_t)index * 4u;
        if (combined <= 0.0) {
            if (palette == 3) {
                pixels[offset] = (uint8_t)((custom_rgb >> 16u) & 0xFFu);
                pixels[offset + 1u] = (uint8_t)((custom_rgb >> 8u) & 0xFFu);
                pixels[offset + 2u] = (uint8_t)(custom_rgb & 0xFFu);
            } else if (palette == 0) {
                pixels[offset] = pixels[offset + 1u] = pixels[offset + 2u] = 0u;
            } else {
                pixels[offset] = pixels[offset + 1u] = pixels[offset + 2u] = 91u;
            }
            pixels[offset + 3u] = 0u;
            continue;
        }
        uint8_t red, green, blue;
        palette_hsl_to_rgb((double)hue, 100.0, 50.0, palette, custom_rgb,
                           &red, &green, &blue);
        pixels[offset] = red;
        pixels[offset + 1u] = green;
        pixels[offset + 2u] = blue;
        pixels[offset + 3u] = (uint8_t)(combined * brightness * 255.0);
    }
    Py_RETURN_NONE;
}

PyObject *render_beat_explosion_into(PyObject *module, PyObject *args) {
    (void)module;
    PyArrayObject *destination;
    int frame, audio_mode, palette;
    double center, radius, width, hit, low, mid, high, brightness;
    unsigned int custom_rgb;
    if (!PyArg_ParseTuple(args, "O!iiddddddddiI", &PyArray_Type, &destination,
                          &frame, &audio_mode, &center, &radius, &width, &hit,
                          &low, &mid, &high, &brightness, &palette,
                          &custom_rgb))
        return NULL;
    if (validate_rgba(destination, "destination", 1) < 0)
        return NULL;
    if ((audio_mode != 0 && audio_mode != 1) || !isfinite(center) ||
        center < 0.0 || !isfinite(radius) || !isfinite(width) || width <= 0.0 ||
        !isfinite(hit) || hit < 0.0 || hit > 1.0 || !isfinite(low) ||
        low < 0.0 || low > 1.0 || !isfinite(mid) || mid < 0.0 || mid > 1.0 ||
        !isfinite(high) || high < 0.0 || high > 1.0 || !isfinite(brightness) ||
        brightness < 0.0 || brightness > 1.0 || palette < 0 || palette > 3 ||
        custom_rgb > 0xFFFFFFu) {
        PyErr_SetString(PyExc_ValueError, "invalid BeatExplosion render parameters");
        return NULL;
    }
    const npy_intp length = PyArray_DIM(destination, 0);
    uint8_t *pixels = (uint8_t *)PyArray_DATA(destination);
    for (npy_intp index = 0; index < length; index++) {
        const double dist = fabs((double)index - center);
        double alpha;
        int hue, lightness;
        if (audio_mode) {
            const double ring = fmax(1.0 - fabs(dist - radius) /
                                     (width + high * 2.0), 0.0);
            const double ambient = fmax(1.0 - dist / fmax(center, 1.0), 0.0) * 0.08;
            alpha = fmin(ambient + ring * (0.15 + hit * 0.75), 1.0);
            const int frame_mod =
                (int)positive_modulo_ssize((Py_ssize_t)frame, 128);
            hue = (10 + (int)(low * 30.0) + frame_mod * 2) % 256;
            lightness = (int)(50.0 + mid * 15.0 + hit * 15.0);
        } else {
            const double ring = fmax(1.0 - fabs(dist - radius) / width, 0.0);
            alpha = ring * ((frame % 30) < 3 ? 0.9 : 0.0);
            const int frame_mod =
                (int)positive_modulo_ssize((Py_ssize_t)frame, 64);
            hue = (frame_mod * 4 + (int)(index % 128) * 2) % 256;
            lightness = 55;
        }
        uint8_t red, green, blue;
        palette_hsl_to_rgb((double)hue, 100.0, (double)lightness, palette,
                           custom_rgb, &red, &green, &blue);
        const size_t offset = (size_t)index * 4u;
        pixels[offset] = red;
        pixels[offset + 1u] = green;
        pixels[offset + 2u] = blue;
        pixels[offset + 3u] = (uint8_t)(alpha * brightness * 255.0);
    }
    Py_RETURN_NONE;
}

PyObject *render_aurora_into(PyObject *module, PyObject *args) {
    return render_aurora_impl(module, args, 0);
}

static PyObject *benchmark_render_aurora_scalar(PyObject *module, PyObject *args) {
    return render_aurora_impl(module, args, 0);
}

static PyObject *benchmark_render_aurora_candidate(PyObject *module,
                                                    PyObject *args) {
    return render_aurora_impl(module, args, 1);
}

static PyObject *render_plasma_rave_impl(PyObject *module, PyObject *args,
                                         int use_neon) {
    (void)module;
    (void)use_neon;
    PyArrayObject *destination;
    double frequency, time_one, time_two, time_three, hue_base, hue_scale;
    double alpha_extra, brightness;
    int palette, clamp_alpha, hue_base_before_truncation;
    unsigned int custom_rgb;
    if (!PyArg_ParseTuple(args, "O!ddddddddiiiI", &PyArray_Type, &destination,
                          &frequency, &time_one, &time_two, &time_three,
                          &hue_base, &hue_scale, &alpha_extra, &brightness,
                          &palette, &clamp_alpha, &hue_base_before_truncation,
                          &custom_rgb))
        return NULL;
    if (validate_rgba(destination, "destination", 1) < 0)
        return NULL;
    if (!isfinite(frequency) || !isfinite(time_one) || !isfinite(time_two) ||
        !isfinite(time_three) || !isfinite(hue_base) || !isfinite(hue_scale) ||
        !isfinite(alpha_extra) || !isfinite(brightness) || frequency <= 0.0 ||
        fabs(hue_base) > (double)INT_MAX / 4.0 ||
        fabs(hue_scale) > (double)INT_MAX / 4.0 ||
        alpha_extra < 0.0 || alpha_extra > 1.0 || brightness < 0.0 ||
        brightness > 1.0 || palette < 0 || palette > 3 ||
        (clamp_alpha != 0 && clamp_alpha != 1) ||
        (hue_base_before_truncation != 0 && hue_base_before_truncation != 1) ||
        custom_rgb > 0xFFFFFFu) {
        PyErr_SetString(PyExc_ValueError, "invalid PlasmaRave render parameters");
        return NULL;
    }
    const npy_intp length = PyArray_DIM(destination, 0);
    const double max_position = length > 1 ? 1.0 : 0.0;
    const double phase_one_start = time_one;
    const double phase_one_end = max_position * 4.0 * frequency + time_one;
    const double phase_two_start = time_two;
    const double phase_two_end = max_position * 8.0 * frequency + time_two;
    const double phase_three_start = time_three * 12.0 * frequency;
    const double phase_three_end =
        (max_position + time_three) * 12.0 * frequency;
    if (!isfinite(phase_one_start) || !isfinite(phase_one_end) ||
        !isfinite(phase_two_start) || !isfinite(phase_two_end) ||
        !isfinite(phase_three_start) || !isfinite(phase_three_end)) {
        PyErr_SetString(PyExc_ValueError, "PlasmaRave phases exceed the finite range");
        return NULL;
    }
    uint8_t *pixels = (uint8_t *)PyArray_DATA(destination);
#if defined(LUMISTRIPE_REACTIVE_NEON)
    double alpha_pair[2];
#endif
    for (npy_intp index = 0; index < length; index++) {
        const double position = (double)index / (double)(length > 1 ? length - 1 : 1);
        const double value_one = sin(position * 4.0 * frequency + time_one);
        const double value_two = sin(position * 8.0 * frequency + time_two);
        const double value_three = sin((position + time_three) * 12.0 * frequency);
        const double plasma = value_one + value_two + value_three;
        const double hue_value = (plasma * 0.5 + 0.5) * hue_scale;
        int hue = hue_base_before_truncation
            ? (int)(hue_value + hue_base)
            : (int)hue_value + (int)hue_base;
        hue %= 256;
        if (hue < 0)
            hue += 256;
        double alpha = 0.4 + fabs(plasma) * 0.3 + alpha_extra;
        if (clamp_alpha && alpha > 1.0)
            alpha = 1.0;
        uint8_t red, green, blue;
        palette_hsl_to_rgb((double)hue, 100.0, 55.0, palette, custom_rgb,
                           &red, &green, &blue);
        const size_t offset = (size_t)index * 4u;
        pixels[offset] = red;
        pixels[offset + 1u] = green;
        pixels[offset + 2u] = blue;
        alpha *= brightness;
#if defined(LUMISTRIPE_REACTIVE_NEON)
        if (use_neon) {
            alpha_pair[index & 1] = alpha;
            if (index & 1)
                store_alpha_pair(pixels, index, alpha_pair, 1.0, 1);
        } else {
            if (alpha > 1.0)
                alpha = 1.0;
            pixels[offset + 3u] = (uint8_t)(alpha * 255.0);
        }
#else
        if (alpha > 1.0)
            alpha = 1.0;
        pixels[offset + 3u] = (uint8_t)(alpha * 255.0);
#endif
    }
#if defined(LUMISTRIPE_REACTIVE_NEON)
    if (use_neon && (length & 1)) {
        double alpha = alpha_pair[0];
        if (alpha > 1.0)
            alpha = 1.0;
        pixels[(size_t)(length - 1) * 4u + 3u] = (uint8_t)(alpha * 255.0);
    }
#endif
    Py_RETURN_NONE;
}

PyObject *render_plasma_rave_into(PyObject *module, PyObject *args) {
    return render_plasma_rave_impl(module, args, 0);
}

static PyObject *benchmark_render_plasma_rave_scalar(PyObject *module,
                                                       PyObject *args) {
    return render_plasma_rave_impl(module, args, 0);
}

static PyObject *benchmark_render_plasma_rave_candidate(PyObject *module,
                                                         PyObject *args) {
    return render_plasma_rave_impl(module, args, 1);
}

static PyObject *render_beat_ripple_impl(PyObject *module, PyObject *args,
                                         int use_neon) {
    (void)module;
    PyArrayObject *destination;
    int audio_mode, hue, lightness, palette;
    double center, radius_or_offset, width, alpha_scale, brightness;
    unsigned int custom_rgb;
    if (!PyArg_ParseTuple(args, "O!idddddiiiI", &PyArray_Type, &destination,
                          &audio_mode, &center, &radius_or_offset, &width,
                          &alpha_scale, &brightness, &hue, &lightness,
                          &palette, &custom_rgb))
        return NULL;
    if (validate_rgba(destination, "destination", 1) < 0)
        return NULL;
    if ((audio_mode != 0 && audio_mode != 1) || !isfinite(center) ||
        !isfinite(radius_or_offset) || !isfinite(width) || width <= 0.0 ||
        !isfinite(alpha_scale) || alpha_scale < 0.0 || !isfinite(brightness) ||
        brightness < 0.0 || brightness > 1.0 || hue < 0 || hue > 255 ||
        lightness < 0 || lightness > 100 || palette < 0 || palette > 3 ||
        custom_rgb > 0xFFFFFFu) {
        PyErr_SetString(PyExc_ValueError, "invalid BeatRipple render parameters");
        return NULL;
    }
    const npy_intp length = PyArray_DIM(destination, 0);
    uint8_t *pixels = (uint8_t *)PyArray_DATA(destination);
    for (npy_intp index = 0; index < length; index++) {
        const size_t offset = (size_t)index * 4u;
        pixels[offset] = pixels[offset + 1u] = pixels[offset + 2u] = 0u;
        pixels[offset + 3u] = 0u;
    }
#if defined(LUMISTRIPE_REACTIVE_NEON)
    if (use_neon && length >= 2) {
        uint8_t red, green, blue;
        palette_hsl_to_rgb((double)hue, 100.0, (double)lightness, palette,
                           custom_rgb, &red, &green, &blue);
        const float64x2_t width_vector = vdupq_n_f64(width);
        const float64x2_t one = vdupq_n_f64(1.0);
        const float64x2_t zero = vdupq_n_f64(0.0);
        const float64x2_t center_vector = vdupq_n_f64(center);
        const float64x2_t radius_vector = vdupq_n_f64(radius_or_offset);
        const float64x2_t mirrored_radius = vdupq_n_f64(
            center + center - radius_or_offset);
        const float64x2_t scale_vector = vdupq_n_f64(alpha_scale);
        const float64x2_t brightness_vector = vdupq_n_f64(brightness);
        npy_intp index = 0;
        for (; index + 1 < length; index += 2) {
            const float64x2_t pixel_index = {
                (double)index, (double)(index + 1)
            };
            float64x2_t intensity;
            if (audio_mode) {
                const float64x2_t left = vabsq_f64(
                    vsubq_f64(pixel_index, radius_vector));
                const float64x2_t right = vabsq_f64(vsubq_f64(
                    vsubq_f64(center_vector, radius_vector), pixel_index));
                intensity = vmaxq_f64(
                    vsubq_f64(one, vdivq_f64(left, width_vector)),
                    vsubq_f64(one, vdivq_f64(right, width_vector)));
            } else {
                const float64x2_t distance = vabsq_f64(
                    vsubq_f64(pixel_index, center_vector));
                const float64x2_t first_delta = vabsq_f64(
                    vsubq_f64(distance, radius_vector));
                const float64x2_t second_delta = vabsq_f64(
                    vsubq_f64(distance, mirrored_radius));
                const float64x2_t first = vmaxq_f64(
                    vsubq_f64(one, vdivq_f64(first_delta, width_vector)), zero);
                const float64x2_t second = vmaxq_f64(
                    vsubq_f64(one, vdivq_f64(second_delta, width_vector)), zero);
                intensity = vmaxq_f64(first, second);
            }
            const float64x2_t alpha = vmulq_f64(intensity, scale_vector);
            const float64x2_t output_alpha = vminq_f64(
                one, vmulq_f64(alpha, brightness_vector));
            double alpha_lanes[2];
            double output_lanes[2];
            vst1q_f64(alpha_lanes, alpha);
            vst1q_f64(output_lanes, output_alpha);
            for (npy_intp lane = 0; lane < 2; lane++) {
                if (alpha_lanes[lane] <= 0.01)
                    continue;
                const size_t offset = (size_t)(index + lane) * 4u;
                pixels[offset] = red;
                pixels[offset + 1u] = green;
                pixels[offset + 2u] = blue;
                pixels[offset + 3u] = (uint8_t)(output_lanes[lane] * 255.0);
            }
        }
        for (; index < length; index++) {
            const double pixel = (double)index;
            double intensity;
            if (audio_mode) {
                const double left = fabs(pixel - radius_or_offset);
                const double right = fabs(center - radius_or_offset - pixel);
                intensity = fmax(1.0 - left / width, 1.0 - right / width);
            } else {
                const double distance = fabs(pixel - center);
                const double first = fmax(
                    1.0 - fabs(distance - radius_or_offset) / width, 0.0);
                const double second = fmax(
                    1.0 - fabs(distance - (center + center - radius_or_offset)) / width,
                    0.0);
                intensity = fmax(first, second);
            }
            const double alpha = intensity * alpha_scale;
            if (alpha <= 0.01)
                continue;
            const size_t offset = (size_t)index * 4u;
            pixels[offset] = red;
            pixels[offset + 1u] = green;
            pixels[offset + 2u] = blue;
            pixels[offset + 3u] = (uint8_t)(fmin(1.0, alpha * brightness) * 255.0);
        }
        Py_RETURN_NONE;
    }
#else
    (void)use_neon;
#endif
    for (npy_intp index = 0; index < length; index++) {
        double intensity;
        if (audio_mode) {
            const double left = fabs((double)index - radius_or_offset);
            const double right = fabs(center - radius_or_offset - (double)index);
            intensity = fmax(1.0 - left / width, 1.0 - right / width);
        } else {
            const double distance = fabs((double)index - center);
            const double first = fmax(1.0 - fabs(distance - radius_or_offset) / width, 0.0);
            const double mirrored_radius = center + center - radius_or_offset;
            const double second = fmax(1.0 - fabs(distance - mirrored_radius) / width, 0.0);
            intensity = fmax(first, second);
        }
        const double alpha = intensity * alpha_scale;
        if (alpha <= 0.01)
            continue;
        uint8_t red, green, blue;
        palette_hsl_to_rgb((double)hue, 100.0, (double)lightness, palette,
                           custom_rgb, &red, &green, &blue);
        const size_t offset = (size_t)index * 4u;
        pixels[offset] = red;
        pixels[offset + 1u] = green;
        pixels[offset + 2u] = blue;
        const double output_alpha = fmin(1.0, alpha * brightness);
        pixels[offset + 3u] = (uint8_t)(output_alpha * 255.0);
    }
    Py_RETURN_NONE;
}

PyObject *render_beat_ripple_into(PyObject *module, PyObject *args) {
    return render_beat_ripple_impl(module, args, 0);
}

static PyObject *benchmark_render_beat_ripple_scalar(PyObject *module,
                                                       PyObject *args) {
    return render_beat_ripple_impl(module, args, 0);
}

static PyObject *benchmark_render_beat_ripple_candidate(PyObject *module,
                                                         PyObject *args) {
    return render_beat_ripple_impl(module, args, 1);
}

PyObject *render_strobe_chase_into(PyObject *module, PyObject *args) {
    (void)module;
    PyArrayObject *destination;
    int stride, hue, flash_active, palette;
    Py_ssize_t frame;
    double phase, drive, flash, brightness;
    unsigned int custom_rgb;
    if (!PyArg_ParseTuple(args, "O!idindddiiI", &PyArray_Type, &destination,
                          &stride, &phase, &hue, &frame, &drive, &flash,
                          &brightness, &flash_active, &palette, &custom_rgb))
        return NULL;
    if (validate_rgba(destination, "destination", 1) < 0)
        return NULL;
    if (stride < 2 || !isfinite(phase) || hue < 0 || hue >= 360 ||
        !isfinite(drive) || drive < 0.0 || drive > 1.0 || !isfinite(flash) ||
        flash < 0.0 || flash > 1.0 || !isfinite(brightness) ||
        brightness < 0.0 || brightness > 1.0 ||
        (flash_active != 0 && flash_active != 1) || palette < 0 || palette > 3 ||
        custom_rgb > 0xFFFFFFu) {
        PyErr_SetString(PyExc_ValueError, "invalid StrobeChase render parameters");
        return NULL;
    }
    const npy_intp length = PyArray_DIM(destination, 0);
    uint8_t *pixels = (uint8_t *)PyArray_DATA(destination);
    static const int club_hues[7] = {330, 18, 48, 126, 190, 224, 286};
    static const int warm_hues[4] = {0, 20, 38, 320};
    if (flash_active) {
        int warm_index = (int)(((int64_t)hue + (int64_t)(frame % 4)) % 4);
        if (warm_index < 0)
            warm_index += 4;
        uint8_t red, green, blue;
        palette_hsl_to_rgb((double)warm_hues[warm_index], 100.0, 60.0,
                           palette, custom_rgb, &red, &green, &blue);
        double alpha = fmin(1.0, 0.12 + flash * 0.88) * brightness;
        if (alpha > 1.0)
            alpha = 1.0;
        for (npy_intp index = 0; index < length; index++) {
            const size_t offset = (size_t)index * 4u;
            pixels[offset] = red;
            pixels[offset + 1u] = green;
            pixels[offset + 2u] = blue;
            pixels[offset + 3u] = (uint8_t)(alpha * 255.0);
        }
        Py_RETURN_NONE;
    }
    for (npy_intp index = 0; index < length; index++) {
        const size_t offset = (size_t)index * 4u;
        pixels[offset] = pixels[offset + 1u] = pixels[offset + 2u] = 0u;
        pixels[offset + 3u] = 255u;
        const int slot = (int)(index % stride);
        const double direct = fabs((double)slot - phase);
        const double distance = fmin(direct, (double)stride - direct);
        const double chase = fmax(1.0 - distance / 1.2, 0.0);
        if (chase <= 0.01)
            continue;
        const int hue_index = (hue + (int)(index % 7) * 11) % 7;
        uint8_t red, green, blue;
        palette_hsl_to_rgb((double)club_hues[hue_index], 100.0, 58.0,
                           palette, custom_rgb, &red, &green, &blue);
        double alpha = chase * (0.34 + drive * 0.5);
        if (alpha > 1.0)
            alpha = 1.0;
        pixels[offset] = red;
        pixels[offset + 1u] = green;
        pixels[offset + 2u] = blue;
        pixels[offset + 3u] = (uint8_t)(alpha * brightness * 255.0);
    }
    Py_RETURN_NONE;
}

PyObject *render_rave_scanner_into(PyObject *module, PyObject *args) {
    (void)module;
    PyArrayObject *destination;
    double phase, width, beam_brightness, brightness;
    int hue, palette;
    unsigned int custom_rgb;
    if (!PyArg_ParseTuple(args, "O!ddddiiI", &PyArray_Type, &destination,
                          &phase, &width, &beam_brightness, &brightness, &hue,
                          &palette, &custom_rgb))
        return NULL;
    if (validate_rgba(destination, "destination", 1) < 0)
        return NULL;
    if (!isfinite(phase) || !isfinite(width) || width <= 0.0 ||
        !isfinite(beam_brightness) || beam_brightness < 0.0 ||
        beam_brightness > 1.0 || !isfinite(brightness) || brightness < 0.0 ||
        brightness > 1.0 || hue < 0 || hue > 255 || palette < 0 || palette > 3 ||
        custom_rgb > 0xFFFFFFu) {
        PyErr_SetString(PyExc_ValueError, "invalid RaveScanner render parameters");
        return NULL;
    }
    static const int club_hues[7] = {330, 18, 48, 126, 190, 224, 286};
    const npy_intp length = PyArray_DIM(destination, 0);
    uint8_t *pixels = (uint8_t *)PyArray_DATA(destination);
    for (npy_intp index = 0; index < length; index++) {
        const size_t offset = (size_t)index * 4u;
        pixels[offset] = pixels[offset + 1u] = pixels[offset + 2u] = 0u;
        pixels[offset + 3u] = 255u;
        const double distance = fabs((double)index - phase);
        if (distance > width)
            continue;
        const double intensity = fmax(0.0, 1.0 - distance / width);
        const double alpha = fmin(
            1.0, beam_brightness * (0.25 + intensity * 0.75));
        const int distance_hue = distance < (double)INT_MAX / 18.0
            ? (int)(distance * 18.0) % 7
            : truncated_modulo_int(fmod(distance, 7.0 / 18.0) * 18.0, 7);
        const int color_index = (hue % 7 + distance_hue) % 7;
        uint8_t red, green, blue;
        palette_hsl_to_rgb((double)club_hues[color_index], 100.0, 60.0,
                           palette, custom_rgb, &red, &green, &blue);
        pixels[offset] = red;
        pixels[offset + 1u] = green;
        pixels[offset + 2u] = blue;
        pixels[offset + 3u] = (uint8_t)(alpha * brightness * 255.0);
    }
    Py_RETURN_NONE;
}

PyObject *render_center_burst_into(PyObject *module, PyObject *args) {
    (void)module;
    PyArrayObject *destination;
    Py_ssize_t hue_seed;
    int palette;
    double center, radius, width, flash, brightness;
    unsigned int custom_rgb;
    if (!PyArg_ParseTuple(args, "O!ndddddiI", &PyArray_Type, &destination,
                          &hue_seed, &center, &radius, &width, &flash,
                          &brightness, &palette, &custom_rgb))
        return NULL;
    if (validate_rgba(destination, "destination", 1) < 0)
        return NULL;
    if (!isfinite(center) || !isfinite(radius) || !isfinite(width) || width <= 0.0 ||
        !isfinite(flash) || flash < 0.0 || flash > 1.0 ||
        !isfinite(brightness) || brightness < 0.0 || brightness > 1.0 ||
        palette < 0 || palette > 3 || custom_rgb > 0xFFFFFFu) {
        PyErr_SetString(PyExc_ValueError, "invalid CenterBurst render parameters");
        return NULL;
    }
    static const int club_hues[7] = {330, 18, 48, 126, 190, 224, 286};
    const npy_intp length = PyArray_DIM(destination, 0);
    uint8_t *pixels = (uint8_t *)PyArray_DATA(destination);
    for (npy_intp index = 0; index < length; index++) {
        const size_t offset = (size_t)index * 4u;
        pixels[offset] = pixels[offset + 1u] = pixels[offset + 2u] = 0u;
        pixels[offset + 3u] = 255u;
        const double distance = fabs((double)index - center);
        const double intensity = fmax(0.0, 1.0 - fabs(distance - radius) / width);
        if (intensity <= 0.0)
            continue;
        const size_t hue_index = (size_t)positive_modulo_ssize(
            positive_modulo_ssize(hue_seed, 7) + index % 7, 7
        );
        uint8_t red, green, blue;
        palette_hsl_to_rgb((double)club_hues[hue_index], 100.0, 60.0,
                           palette, custom_rgb, &red, &green, &blue);
        const double alpha = fmin(1.0, intensity * (0.28 + flash * 0.72)) * brightness;
        pixels[offset] = red;
        pixels[offset + 1u] = green;
        pixels[offset + 2u] = blue;
        pixels[offset + 3u] = (uint8_t)(alpha * 255.0);
    }
    Py_RETURN_NONE;
}

PyObject *render_dual_laser_into(PyObject *module, PyObject *args) {
    (void)module;
    PyArrayObject *destination;
    double position_a, position_b, width, alpha, brightness;
    int hue_a, hue_b, palette;
    unsigned int custom_rgb;
    if (!PyArg_ParseTuple(args, "O!dddddiiiI", &PyArray_Type, &destination,
                          &position_a, &position_b, &width, &alpha, &brightness,
                          &hue_a, &hue_b, &palette, &custom_rgb))
        return NULL;
    if (validate_rgba(destination, "destination", 1) < 0)
        return NULL;
    if (!isfinite(position_a) || !isfinite(position_b) || !isfinite(width) ||
        width <= 0.0 || !isfinite(alpha) || alpha < 0.0 || alpha > 1.0 ||
        !isfinite(brightness) || brightness < 0.0 || brightness > 1.0 ||
        hue_a < 0 || hue_a > 255 || hue_b < 0 || hue_b > 255 ||
        palette < 0 || palette > 3 || custom_rgb > 0xFFFFFFu) {
        PyErr_SetString(PyExc_ValueError, "invalid DualLaser render parameters");
        return NULL;
    }
    const npy_intp length = PyArray_DIM(destination, 0);
    uint8_t *pixels = (uint8_t *)PyArray_DATA(destination);
    for (npy_intp index = 0; index < length; index++) {
        const size_t offset = (size_t)index * 4u;
        pixels[offset] = pixels[offset + 1u] = pixels[offset + 2u] = 0u;
        pixels[offset + 3u] = 255u;
    }
    for (int beam = 0; beam < 2; beam++) {
        const double position = beam == 0 ? position_a : position_b;
        const int hue = beam == 0 ? hue_a : hue_b;
        for (npy_intp index = 0; index < length; index++) {
            const double distance = fabs((double)index - position);
            if (distance >= width)
                continue;
            const double intensity = 1.0 - distance / width;
            uint8_t red, green, blue;
            palette_hsl_to_rgb((double)hue, 100.0, 60.0, palette,
                               custom_rgb, &red, &green, &blue);
            const size_t offset = (size_t)index * 4u;
            pixels[offset] = red;
            pixels[offset + 1u] = green;
            pixels[offset + 2u] = blue;
            pixels[offset + 3u] = (uint8_t)(intensity * alpha * brightness * 255.0);
        }
    }
    Py_RETURN_NONE;
}

PyObject *render_mirror_flash_into(PyObject *module, PyObject *args) {
    (void)module;
    PyArrayObject *destination;
    Py_ssize_t hue_seed;
    int alternate, palette;
    double center, span, flash, brightness;
    unsigned int custom_rgb;
    if (!PyArg_ParseTuple(args, "O!nddddiiI", &PyArray_Type, &destination,
                          &hue_seed, &center, &span, &flash, &brightness,
                          &alternate, &palette, &custom_rgb))
        return NULL;
    if (validate_rgba(destination, "destination", 1) < 0)
        return NULL;
    if (!isfinite(center) || !isfinite(span) || span <= 0.0 ||
        !isfinite(flash) || flash < 0.0 || flash > 1.0 ||
        !isfinite(brightness) || brightness < 0.0 || brightness > 1.0 ||
        (alternate != 0 && alternate != 1) || palette < 0 || palette > 3 ||
        custom_rgb > 0xFFFFFFu) {
        PyErr_SetString(PyExc_ValueError, "invalid MirrorFlash render parameters");
        return NULL;
    }
    static const int club_hues[7] = {330, 18, 48, 126, 190, 224, 286};
    static const int warm_hues[4] = {0, 20, 38, 320};
    const npy_intp length = PyArray_DIM(destination, 0);
    uint8_t *pixels = (uint8_t *)PyArray_DATA(destination);
    for (npy_intp index = 0; index < length; index++) {
        const size_t offset = (size_t)index * 4u;
        pixels[offset] = pixels[offset + 1u] = pixels[offset + 2u] = 0u;
        pixels[offset + 3u] = 255u;
    }
    for (npy_intp index = 0; index < (length + 1) / 2; index++) {
        const double distance = fabs((double)index - center);
        const double intensity = fmax(0.0, 1.0 - distance / span) * flash;
        if (intensity <= 0.0)
            continue;
        const double alpha = fmin(1.0, 0.14 + intensity * 0.86);
        const npy_intp mirror = length - 1 - index > 0 ? length - 1 - index : 0;
        uint8_t red, green, blue;
        const Py_ssize_t color_offset = alternate
            ? (index % 7) * 13
            : -(index % 7) * 9;
        const Py_ssize_t color_hue = positive_modulo_ssize(
            positive_modulo_ssize(hue_seed, 7) + color_offset, 7
        );
        palette_hsl_to_rgb((double)club_hues[color_hue], 100.0, 62.0,
                           palette, custom_rgb, &red, &green, &blue);
        size_t offset = (size_t)index * 4u;
        pixels[offset] = red;
        pixels[offset + 1u] = green;
        pixels[offset + 2u] = blue;
        pixels[offset + 3u] = (uint8_t)(alpha * brightness * 255.0);
        if (mirror != index) {
            if (alternate) {
                const Py_ssize_t warm_index = positive_modulo_ssize(
                    positive_modulo_ssize(hue_seed, 4) + (index % 4) * 13 + 11,
                    4
                );
                palette_hsl_to_rgb((double)warm_hues[warm_index], 100.0, 60.0,
                                   palette, custom_rgb, &red, &green, &blue);
            }
            offset = (size_t)mirror * 4u;
            pixels[offset] = red;
            pixels[offset + 1u] = green;
            pixels[offset + 2u] = blue;
            pixels[offset + 3u] = (uint8_t)(alpha * brightness * 255.0);
        }
    }
    Py_RETURN_NONE;
}

PyObject *render_drop_wave_into(PyObject *module, PyObject *args) {
    (void)module;
    PyArrayObject *destination;
    Py_ssize_t hue_seed;
    int palette;
    double center, radius, width, charge, flash, brightness;
    unsigned int custom_rgb;
    if (!PyArg_ParseTuple(args, "O!nddddddiI", &PyArray_Type, &destination,
                          &hue_seed, &center, &radius, &width, &charge, &flash,
                          &brightness, &palette, &custom_rgb))
        return NULL;
    if (validate_rgba(destination, "destination", 1) < 0)
        return NULL;
    if (!isfinite(center) || !isfinite(radius) || !isfinite(width) || width <= 0.0 ||
        !isfinite(charge) || charge < 0.0 || charge > 1.0 ||
        !isfinite(flash) || flash < 0.0 || flash > 1.0 ||
        !isfinite(brightness) || brightness < 0.0 || brightness > 1.0 ||
        palette < 0 || palette > 3 || custom_rgb > 0xFFFFFFu) {
        PyErr_SetString(PyExc_ValueError, "invalid DropWave render parameters");
        return NULL;
    }
    static const int club_hues[7] = {330, 18, 48, 126, 190, 224, 286};
    static const int warm_hues[4] = {0, 20, 38, 320};
    const npy_intp length = PyArray_DIM(destination, 0);
    uint8_t *pixels = (uint8_t *)PyArray_DATA(destination);
    for (npy_intp index = 0; index < length; index++) {
        const size_t offset = (size_t)index * 4u;
        pixels[offset] = pixels[offset + 1u] = pixels[offset + 2u] = 0u;
        pixels[offset + 3u] = 255u;
        const double distance = fabs((double)index - center);
        double alpha = 0.0;
        int hue;
        int lightness;
        if (flash > 0.0) {
            const double intensity = fmax(
                0.0, 1.0 - fabs(distance - radius) / (width + charge * width));
            if (intensity <= 0.0)
                continue;
            alpha = fmin(1.0, intensity * (0.22 + flash * 0.78));
            const Py_ssize_t color_index = positive_modulo_ssize(
                positive_modulo_ssize(hue_seed, 4) + index % 4, 4
            );
            hue = warm_hues[color_index];
            lightness = 60;
        } else if (charge > 0.35) {
            const double pre = fmax(0.0, 1.0 - distance / fmax(center, 1.0));
            alpha = fmin(1.0, pre * (0.08 + charge * 0.2));
            if (alpha <= 0.0)
                continue;
            const Py_ssize_t color_index = positive_modulo_ssize(
                positive_modulo_ssize(hue_seed, 7) + index % 7, 7
            );
            hue = club_hues[color_index];
            lightness = 48;
        } else {
            continue;
        }
        uint8_t red, green, blue;
        palette_hsl_to_rgb((double)hue, 100.0, (double)lightness, palette,
                           custom_rgb, &red, &green, &blue);
        pixels[offset] = red;
        pixels[offset + 1u] = green;
        pixels[offset + 2u] = blue;
        pixels[offset + 3u] = (uint8_t)(alpha * brightness * 255.0);
    }
    Py_RETURN_NONE;
}

PyObject *render_lightning_strike_into(PyObject *module, PyObject *args) {
    (void)module;
    PyArrayObject *destination;
    Py_ssize_t strike_position;
    int strike_length, hue, audio_mode, palette;
    double flash_alpha, all_flash, brightness;
    unsigned int custom_rgb;
    if (!PyArg_ParseTuple(args, "O!niidddiiI", &PyArray_Type, &destination,
                          &strike_position, &strike_length, &hue, &flash_alpha,
                          &all_flash, &brightness, &audio_mode, &palette,
                          &custom_rgb))
        return NULL;
    if (validate_rgba(destination, "destination", 1) < 0)
        return NULL;
    if (strike_length < 0 || hue < 0 || hue > 255 || !isfinite(flash_alpha) ||
        flash_alpha < 0.0 || flash_alpha > 1.0 || !isfinite(all_flash) ||
        all_flash < 0.0 || all_flash > 1.5 ||
        !isfinite(brightness) || brightness < 0.0 ||
        brightness > 1.0 || (audio_mode != 0 && audio_mode != 1) ||
        palette < 0 || palette > 3 || custom_rgb > 0xFFFFFFu) {
        PyErr_SetString(PyExc_ValueError, "invalid LightningStrike parameters");
        return NULL;
    }
    const double strike_threshold = audio_mode ? 0.05 : 0.1;
    const npy_intp length = PyArray_DIM(destination, 0);
    uint8_t *pixels = (uint8_t *)PyArray_DATA(destination);
    for (npy_intp index = 0; index < length; index++) {
        const double distance = fabs((double)index - (double)strike_position);
        uint8_t red, green, blue;
        double alpha;
        if (distance < (double)strike_length && flash_alpha > strike_threshold) {
            double intensity = 1.0 - distance / (double)(strike_length > 1 ? strike_length : 1);
            if (audio_mode && intensity < 0.0)
                intensity = 0.0;
            alpha = intensity * flash_alpha;
            palette_hsl_to_rgb((double)hue, 80.0, 70.0, palette, custom_rgb,
                               &red, &green, &blue);
        } else if (all_flash > 0.0) {
            alpha = all_flash * 0.4;
            palette_hsl_to_rgb((double)hue, 50.0, 80.0, palette, custom_rgb,
                               &red, &green, &blue);
        } else {
            const size_t offset = (size_t)index * 4u;
            uint8_t red = 0u, green = 0u, blue = 0u;
            if (palette == 1 || palette == 2) {
                red = green = blue = 91u;
            } else if (palette == 3) {
                red = (uint8_t)((custom_rgb >> 16u) & 0xFFu);
                green = (uint8_t)((custom_rgb >> 8u) & 0xFFu);
                blue = (uint8_t)(custom_rgb & 0xFFu);
            }
            pixels[offset] = red;
            pixels[offset + 1u] = green;
            pixels[offset + 2u] = blue;
            pixels[offset + 3u] = 0u;
            continue;
        }
        const size_t offset = (size_t)index * 4u;
        pixels[offset] = red;
        pixels[offset + 1u] = green;
        pixels[offset + 2u] = blue;
        pixels[offset + 3u] = (uint8_t)(alpha * brightness * 255.0);
    }
    Py_RETURN_NONE;
}

PyObject *render_neon_storm_into(PyObject *module, PyObject *args) {
    (void)module;
    PyArrayObject *destination, *levels, *hues, *positions;
    double drive, brightness;
    int palette;
    unsigned int custom_rgb;
    if (!PyArg_ParseTuple(args, "O!O!O!O!ddiI", &PyArray_Type, &destination,
                          &PyArray_Type, &levels, &PyArray_Type, &hues,
                          &PyArray_Type, &positions, &drive, &brightness,
                          &palette, &custom_rgb))
        return NULL;
    if (validate_rgba(destination, "destination", 1) < 0) return NULL;
    const npy_intp count = PyArray_DIM(destination, 0);
    const npy_intp particle_shape[1] = {count};
    const npy_intp position_shape[1] = {-1};
    if (validate_array(levels, NPY_FLOAT32, 1, particle_shape, 0, 0.0, 1.0,
                       "levels") < 0 ||
        validate_int16_array_range(hues, 1, particle_shape, 0, 0, 4, "hues") < 0 ||
        validate_array(positions, NPY_FLOAT64, 1, position_shape, 0, 0.0,
                       count > 0 ? (double)count : 0.0, "positions") < 0 ||
        reject_any_overlap(destination, levels, "destination", "levels") < 0 ||
        reject_any_overlap(destination, hues, "destination", "hues") < 0 ||
        reject_any_overlap(destination, positions, "destination", "positions") < 0 ||
        reject_any_overlap(levels, hues, "levels", "hues") < 0 ||
        reject_any_overlap(levels, positions, "levels", "positions") < 0 ||
        reject_any_overlap(hues, positions, "hues", "positions") < 0 ||
        !isfinite(drive) ||
        !isfinite(brightness) || brightness < 0.0 || brightness > 1.0 ||
        palette < 0 || palette > 3 || custom_rgb > 0xFFFFFFu) {
        PyErr_SetString(PyExc_ValueError, "invalid NeonStorm buffers or parameters");
        return NULL;
    }
    static const int palette_hues[5] = {0, 200, 120, 300, 50};
    static const int palette_light[5] = {60, 55, 50, 60, 55};
    const float *particle = (const float *)PyArray_DATA(levels);
    const int16_t *particle_hue = (const int16_t *)PyArray_DATA(hues);
    const double *streaks = (const double *)PyArray_DATA(positions);
    const npy_intp streak_count = PyArray_DIM(positions, 0);
    uint8_t *pixels = (uint8_t *)PyArray_DATA(destination);
    for (npy_intp index = 0; index < count; index++) {
        double streak = 0.0;
        npy_intp streak_seed = 0;
        for (npy_intp s = 0; s < streak_count; s++) {
            double distance = fabs((double)index - streaks[s]);
            const double wrapped = (double)count - distance;
            if (wrapped < distance) distance = wrapped;
            if (distance < 3.5) {
                const double strength = 1.0 - distance / 3.5;
                if (strength > streak) { streak = strength; streak_seed = s; }
            }
        }
        const double p = particle[index];
        const double alpha = fmin(1.0, p * 0.8 + streak * (0.35 + drive * 0.3));
        const size_t offset = (size_t)index * 4u;
        if (alpha <= 0.01) {
            uint8_t clear_r = 0u, clear_g = 0u, clear_b = 0u;
            if (palette == 1 || palette == 2) clear_r = clear_g = clear_b = 91u;
            else if (palette == 3) {
                clear_r = (uint8_t)(custom_rgb >> 16u);
                clear_g = (uint8_t)(custom_rgb >> 8u);
                clear_b = (uint8_t)custom_rgb;
            }
            pixels[offset] = clear_r; pixels[offset + 1u] = clear_g;
            pixels[offset + 2u] = clear_b; pixels[offset + 3u] = 0u;
            continue;
        }
        int selected = (p >= streak
            ? particle_hue[index]
            : (int)(streak_seed % 5)) % 5;
        if (selected < 0) selected += 5;
        uint8_t red, green, blue;
        catalog_palette_hsl_to_rgb((double)palette_hues[selected], 100.0,
                                   (double)palette_light[selected], palette,
                                   custom_rgb, &red, &green, &blue);
        pixels[offset] = red; pixels[offset + 1u] = green; pixels[offset + 2u] = blue;
        pixels[offset + 3u] = (uint8_t)(alpha * brightness * 255.0);
    }
    Py_RETURN_NONE;
}

PyObject *render_electric_storm_into(PyObject *module, PyObject *args) {
    (void)module;
    PyArrayObject *destination, *geometry;
    double flash_alpha, brightness;
    int palette;
    unsigned int custom_rgb;
    if (!PyArg_ParseTuple(args, "O!O!ddiI", &PyArray_Type, &destination,
                          &PyArray_Type, &geometry, &flash_alpha, &brightness,
                          &palette, &custom_rgb)) return NULL;
    if (validate_rgba(destination, "destination", 1) < 0) return NULL;
    const npy_intp geometry_shape[2] = {-1, 4};
    if (validate_array(geometry, NPY_FLOAT64, 2, geometry_shape, 0,
                       -INFINITY, INFINITY, "geometry") < 0 ||
        reject_any_overlap(destination, geometry, "destination", "geometry") < 0 ||
        !isfinite(flash_alpha) || flash_alpha < 0.0 || flash_alpha > 1.0 ||
        !isfinite(brightness) || brightness < 0.0 || brightness > 1.0 ||
        palette < 0 || palette > 3 || custom_rgb > 0xFFFFFFu) {
        PyErr_SetString(PyExc_ValueError, "invalid ElectricStorm buffers or parameters");
        return NULL;
    }
    const npy_intp count = PyArray_DIM(destination, 0);
    const npy_intp streak_count = PyArray_DIM(geometry, 0);
    const double *streaks = (const double *)PyArray_DATA(geometry);
    for (npy_intp s = 0; s < streak_count; s++) {
        const double pos = streaks[s * 4];
        const double run = streaks[s * 4 + 1];
        const double alpha = streaks[s * 4 + 3];
        if (pos < 0.0 || pos > (double)INT_MAX || run < 0.0 ||
            run > (double)INT_MAX || alpha < 0.0 || alpha > 1.0) {
            PyErr_SetString(PyExc_ValueError,
                            "ElectricStorm geometry is outside the supported range");
            return NULL;
        }
    }
    uint8_t *pixels = (uint8_t *)PyArray_DATA(destination);
    uint8_t clear_r = 0u, clear_g = 0u, clear_b = 0u;
    if (palette == 1 || palette == 2) clear_r = clear_g = clear_b = 91u;
    else if (palette == 3) {
        clear_r = (uint8_t)(custom_rgb >> 16u);
        clear_g = (uint8_t)(custom_rgb >> 8u);
        clear_b = (uint8_t)custom_rgb;
    }
    uint8_t flash_r, flash_g, flash_b;
    catalog_palette_hsl_to_rgb(230.0, 80.0, 80.0, palette, custom_rgb, &flash_r, &flash_g, &flash_b);
    for (npy_intp i = 0; i < count; i++) {
        const size_t offset = (size_t)i * 4u;
        pixels[offset] = flash_alpha > 0.0 ? flash_r : clear_r;
        pixels[offset + 1u] = flash_alpha > 0.0 ? flash_g : clear_g;
        pixels[offset + 2u] = flash_alpha > 0.0 ? flash_b : clear_b;
        pixels[offset + 3u] = (uint8_t)(flash_alpha * brightness * 255.0);
    }
    for (npy_intp s = 0; s < streak_count; s++) {
        const double pos = streaks[s * 4];
        const npy_intp length = (npy_intp)streaks[s * 4 + 1];
        const double hue = streaks[s * 4 + 2];
        const double alpha = streaks[s * 4 + 3];
        uint8_t red, green, blue;
        catalog_palette_hsl_to_rgb(hue, 90.0, 75.0, palette, custom_rgb, &red, &green, &blue);
        for (npy_intp j = 0; j < length; j++) {
            const npy_intp pixel = (npy_intp)pos - j;
            if (pixel < 0 || pixel >= count) continue;
            const double intensity = 1.0 - (double)j / (double)(length > 1 ? length : 1);
            const size_t offset = (size_t)pixel * 4u;
            pixels[offset] = red; pixels[offset + 1u] = green; pixels[offset + 2u] = blue;
            pixels[offset + 3u] = (uint8_t)(intensity * alpha * brightness * 255.0);
        }
    }
    Py_RETURN_NONE;
}

static PyMethodDef methods[] = {
    {"render_shockwave_into", render_shockwave_into, METH_VARARGS,
     "Render a radial Shockwave ring and ambient field into an RGBA frame."},
    {"render_aurora_into", render_aurora_into, METH_VARARGS,
     "Render Aurora curtain waves with optional audio band modulation."},
    {"render_spectrum_flash_into", render_spectrum_flash_into, METH_VARARGS,
     "Render SpectrumFlash spectrum and flash pixels into an RGBA frame."},
    {"render_pulse_into", render_pulse_into, METH_VARARGS,
     "Render the spatial Pulse field into an RGBA frame."},
    {"render_dance_floor_into", render_dance_floor_into, METH_VARARGS,
     "Render segmented DanceFloor colors into an RGBA frame."},
    {"render_club_flash_into", render_club_flash_into, METH_VARARGS,
     "Render ClubFlash whole-strip or segmented mirrored output."},
    {"render_beat_wave_into", render_beat_wave_into, METH_VARARGS,
     "Render up to eight audio-triggered BeatWave rings."},
    {"render_beat_explosion_into", render_beat_explosion_into, METH_VARARGS,
     "Render the static or audio-reactive BeatExplosion ring."},
    {"render_plasma_rave_into", render_plasma_rave_into, METH_VARARGS,
     "Render PlasmaRave's static or audio-reactive plasma field."},
    {"render_beat_ripple_into", render_beat_ripple_into, METH_VARARGS,
     "Render BeatRipple's mirrored static or audio-reactive rings."},
    {"render_strobe_chase_into", render_strobe_chase_into, METH_VARARGS,
     "Render StrobeChase's moving pulses or full-frame flash."},
    {"render_rave_scanner_into", render_rave_scanner_into, METH_VARARGS,
     "Render RaveScanner's moving beam with a beat-reactive flash envelope."},
    {"render_center_burst_into", render_center_burst_into, METH_VARARGS,
     "Render CenterBurst's expanding ring around the strip center."},
    {"render_dual_laser_into", render_dual_laser_into, METH_VARARGS,
     "Render two independently colored beams into a cleared RGBA frame."},
    {"render_mirror_flash_into", render_mirror_flash_into, METH_VARARGS,
     "Render MirrorFlash as a symmetric colored pulse."},
    {"render_drop_wave_into", render_drop_wave_into, METH_VARARGS,
     "Render DropWave's precharge and expanding flash ring."},
    {"render_lightning_strike_into", render_lightning_strike_into, METH_VARARGS,
     "Render LightningStrike's bolt flash or ambient fade."},
    {"render_neon_storm_into", render_neon_storm_into, METH_VARARGS,
     "Render NeonStorm into a reusable RGBA destination."},
    {"render_electric_storm_into", render_electric_storm_into, METH_VARARGS,
     "Render ElectricStorm flashes and streaks into a reusable RGBA destination."},
    {"_benchmark_render_aurora_scalar", benchmark_render_aurora_scalar, METH_VARARGS,
     "Scalar Aurora reference kernel for the native SIMD benchmark."},
    {"_benchmark_render_plasma_rave_scalar", benchmark_render_plasma_rave_scalar,
     METH_VARARGS, "Scalar PlasmaRave reference kernel for the native SIMD benchmark."},
    {"_benchmark_render_beat_ripple_scalar", benchmark_render_beat_ripple_scalar,
     METH_VARARGS, "Scalar BeatRipple reference kernel for the native SIMD benchmark."},
    {"_benchmark_render_aurora_candidate", benchmark_render_aurora_candidate,
     METH_VARARGS, "Unreleased Aurora SIMD candidate for benchmark comparison."},
    {"_benchmark_render_plasma_rave_candidate", benchmark_render_plasma_rave_candidate,
     METH_VARARGS, "Unreleased PlasmaRave SIMD candidate for benchmark comparison."},
    {"_benchmark_render_beat_ripple_candidate", benchmark_render_beat_ripple_candidate,
     METH_VARARGS, "Unreleased BeatRipple SIMD candidate for benchmark comparison."},
    {NULL, NULL, 0, NULL}
};

PyMethodDef *native_reactive_methods(void) { return methods; }
