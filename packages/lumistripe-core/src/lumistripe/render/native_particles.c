#include "native_common.h"
PyObject *render_confetti_into(PyObject *module, PyObject *args) {
    (void)module;
    PyArrayObject *destination, *intensity_array;
    PyObject *bands_object;
    Py_ssize_t frame;
    int audio_mode, density, hue_shift, palette;
    double fade, spread, trigger_rate, addition, accent, high, bloom, brightness;
    unsigned int custom_rgb;
    if (!PyArg_ParseTuple(args, "O!O!OnidddiddddidiI", &PyArray_Type, &destination,
                          &PyArray_Type, &intensity_array, &bands_object, &frame, &audio_mode,
                          &fade, &spread, &trigger_rate, &density, &addition,
                          &accent, &high, &bloom, &hue_shift, &brightness,
                          &palette, &custom_rgb))
        return NULL;
    if (validate_rgba(destination, "destination", 1) < 0)
        return NULL;
    const npy_intp intensity_shape[1] = {PyArray_DIM(destination, 0)};
    if (validate_array(intensity_array, NPY_FLOAT32, 1, intensity_shape, 1,
                       0.0, 1.0, "intensities") < 0 ||
        reject_any_overlap(destination, intensity_array, "destination", "intensities") < 0) {
        return NULL;
    }
    if ((audio_mode != 0 && audio_mode != 1) || !isfinite(fade) || fade < 0.0 ||
        fade > 1.0 || !isfinite(spread) || spread < 0.0 || spread > 1.0 ||
        !isfinite(trigger_rate) || trigger_rate < 0.0 || trigger_rate > 100.0 ||
        density < 0 || density > 100 || !isfinite(addition) || addition < 0.0 ||
        addition > 1.0 || !isfinite(accent) || accent < 0.0 || accent > 1.0 ||
        !isfinite(high) || high < 0.0 || high > 1.0 || !isfinite(bloom) ||
        bloom < 0.0 || bloom > 1.0 || !isfinite(brightness) ||
        brightness < 0.0 || brightness > 1.0 || hue_shift < 0 || hue_shift > 255 ||
        palette < 0 || palette > 3 || custom_rgb > 0xFFFFFFu) {
        PyErr_SetString(PyExc_ValueError, "invalid Confetti render parameters");
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
                    PyErr_SetString(PyExc_ValueError, "bands must be normalized finite values");
                return NULL;
            }
        }
        Py_DECREF(sequence);
    }
    const npy_intp count = PyArray_DIM(destination, 0);
    uint8_t *pixels = (uint8_t *)PyArray_DATA(destination);
    float *intensities = (float *)PyArray_DATA(intensity_array);
    const double decay = audio_mode
        ? fmax(0.68, 0.68 + fade * 0.30 - high * 0.03)
        : 0.72 + fade * 0.32;
    const npy_intp center = count > 0
        ? multiply_modulo_ssize(frame, 37u, count)
        : 0;
    for (npy_intp index = 0; index < count; index++) {
        const uint64_t seed = (uint64_t)frame * UINT64_C(1103515245) +
                              (uint64_t)index * UINT64_C(12345);
        const npy_intp direct = llabs((long long)index - (long long)center);
        const npy_intp wrapped = count - direct;
        const npy_intp distance = direct < wrapped ? direct : wrapped;
        const double spread_weight = (1.0 - spread) + spread *
            fmax(0.0, 1.0 - (double)distance / fmax((double)count * 0.5, 1.0));
        float intensity = intensities[index] * (float)decay;
        const int trigger = audio_mode
            ? (accent > 0.55 || (double)(seed % 100u) < (double)density * spread_weight)
            : (double)(seed % 100u) < trigger_rate * spread_weight;
        if (trigger) {
            if (audio_mode) {
                intensity = (float)(intensity + (float)0.4);
                intensity = (float)(intensity + (float)(high * 0.15));
            } else {
                intensity = (float)(intensity + (float)addition);
            }
            if (intensity > 1.0f)
                intensity = 1.0f;
        }
        intensities[index] = intensity;
        double alpha = 0.0;
        uint8_t red = 0u, green = 0u, blue = 0u;
        if (intensity > 0.03f) {
            int hue;
            int lightness;
            if (audio_mode) {
                double local;
                if (count <= 1) {
                    local = bands[0];
                } else {
                    const double band_position = (double)index * 8.0 / (double)(count - 1);
                    const int band_center = (int)band_position;
                    const int band_start = band_center > 0 ? band_center - 1 : 0;
                    const int band_end = band_center + 2 < 8 ? band_center + 2 : 8;
                    local = 0.0;
                    for (int band = band_start; band < band_end; band++)
                        local += bands[band];
                    local /= (double)(band_end - band_start);
                }
                hue = (int)((seed / 100u + (uint64_t)hue_shift +
                             (uint64_t)(int)(local * 18.0)) % 256u);
                lightness = 46 + (int)((double)intensity * 20.0 + high * 8.0);
                alpha = fmin(1.0, 0.12 + (double)intensity * 0.78 + bloom * 0.08);
            } else {
                hue = (int)((seed / 100u + (uint64_t)frame * 2u) % 256u);
                lightness = 48 + (int)((double)intensity * 16.0);
                alpha = fmin(1.0, 0.12 + (double)intensity * 0.88);
            }
            hsl_to_rgb((double)hue, 100.0, (double)lightness, &red, &green, &blue);
            if (palette == 3) {
                red = (uint8_t)((custom_rgb >> 16u) & 0xFFu);
                green = (uint8_t)((custom_rgb >> 8u) & 0xFFu);
                blue = (uint8_t)(custom_rgb & 0xFFu);
            } else if (palette == 1 || palette == 2) {
                map_palette(&red, &green, &blue, palette);
            }
        } else if (palette == 3) {
            red = (uint8_t)((custom_rgb >> 16u) & 0xFFu);
            green = (uint8_t)((custom_rgb >> 8u) & 0xFFu);
            blue = (uint8_t)(custom_rgb & 0xFFu);
        } else if (palette == 1 || palette == 2) {
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

PyObject *render_glow_rush_into(PyObject *module, PyObject *args) {
    (void)module;
    PyArrayObject *destination;
    double position, width, pulse, glow_width, brightness, glow_alpha;
    int hue, audio_mode, main_light, palette;
    unsigned int custom_rgb;
    if (!PyArg_ParseTuple(args, "O!ddidddiidiI", &PyArray_Type, &destination,
                          &position, &width, &hue, &pulse, &glow_width,
                          &brightness, &audio_mode, &main_light, &glow_alpha,
                          &palette, &custom_rgb))
        return NULL;
    if (validate_rgba(destination, "destination", 1) < 0)
        return NULL;
    if (!isfinite(position) || width <= 0 || hue < 0 || hue > 255 ||
        !isfinite(pulse) || pulse < 0.0 || pulse > 1.0 ||
        !isfinite(glow_width) || glow_width <= 0.0 || !isfinite(brightness) ||
        brightness < 0.0 || brightness > 1.0 || (audio_mode != 0 && audio_mode != 1) ||
        main_light < 0 || main_light > 100 || !isfinite(glow_alpha) ||
        glow_alpha < 0.0 || glow_alpha > 1.0 ||
        palette < 0 || palette > 3 || custom_rgb > 0xFFFFFFu) {
        PyErr_SetString(PyExc_ValueError, "invalid GlowRush render parameters");
        return NULL;
    }
    uint8_t *pixels = (uint8_t *)PyArray_DATA(destination);
    const npy_intp count = PyArray_DIM(destination, 0);
    for (npy_intp index = 0; index < count; index++) {
        const double distance = fabs((double)index - position);
        double tail = fmax(0.0, 1.0 - distance / (double)width);
        tail *= tail;
        double alpha;
        int lightness;
        if (tail > 0.01) {
            alpha = fmin(tail * pulse, 1.0);
            lightness = main_light;
        } else {
            alpha = fmax(0.0, 1.0 - distance / glow_width) * glow_alpha;
            if (alpha <= 0.01)
                continue;
            lightness = audio_mode ? 20 : 30;
        }
        uint8_t red, green, blue;
        if (palette == 3) {
            red = (uint8_t)((custom_rgb >> 16u) & 0xFFu);
            green = (uint8_t)((custom_rgb >> 8u) & 0xFFu);
            blue = (uint8_t)(custom_rgb & 0xFFu);
        } else {
            hsl_to_rgb((double)hue, 100.0, (double)lightness, &red, &green, &blue);
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

PyObject *render_dual_comet_into(PyObject *module, PyObject *args) {
    (void)module;
    PyArrayObject *destination;
    double phase, width, alpha, separation, brightness;
    int hue_a, hue_b, palette;
    unsigned int custom_rgb;
    if (!PyArg_ParseTuple(args, "O!ddddiidiI", &PyArray_Type, &destination,
                          &phase, &width, &alpha, &separation, &hue_a, &hue_b,
                          &brightness, &palette, &custom_rgb))
        return NULL;
    if (validate_rgba(destination, "destination", 1) < 0)
        return NULL;
    if (!isfinite(phase) || !isfinite(width) || width <= 0.0 ||
        !isfinite(alpha) || alpha < 0.0 || alpha > 1.0 ||
        !isfinite(separation) || !isfinite(brightness) || brightness < 0.0 ||
        brightness > 1.0 || hue_a < 0 || hue_a > 359 || hue_b < 0 || hue_b > 359 ||
        palette < 0 || palette > 3 || custom_rgb > 0xFFFFFFu) {
        PyErr_SetString(PyExc_ValueError, "invalid DualComet render parameters");
        return NULL;
    }
    const npy_intp count = PyArray_DIM(destination, 0);
    const double max_pos = (double)(count > 0 ? count - 1 : 0);
    double other = fmod(phase + max_pos * separation, fmax(max_pos + 1.0, 1.0));
    if (other < 0.0)
        other += fmax(max_pos + 1.0, 1.0);
    uint8_t *pixels = (uint8_t *)PyArray_DATA(destination);
    for (npy_intp index = 0; index < count; index++) {
        const double distance_a = fabs((double)index - phase);
        const double distance_b = fabs((double)index - other);
        const double tail_a = fmax(1.0 - distance_a / width, 0.0);
        const double tail_b = fmax(1.0 - distance_b / width, 0.0);
        double pixel_alpha = 0.0;
        int hue = 0;
        if (tail_a > tail_b && tail_a > 0.0) {
            pixel_alpha = fmin(tail_a * alpha, 1.0);
            hue = hue_a;
        } else if (tail_b > 0.0) {
            pixel_alpha = fmin(tail_b * alpha, 1.0);
            hue = hue_b;
        }
        uint8_t red = 0u, green = 0u, blue = 0u;
        if (palette == 3) {
            red = (uint8_t)((custom_rgb >> 16u) & 0xFFu);
            green = (uint8_t)((custom_rgb >> 8u) & 0xFFu);
            blue = (uint8_t)(custom_rgb & 0xFFu);
        } else if (pixel_alpha > 0.0) {
            hsl_to_rgb((double)hue, 100.0, 55.0, &red, &green, &blue);
            if (palette == 1 || palette == 2)
                map_palette(&red, &green, &blue, palette);
        } else if (palette == 1 || palette == 2) {
            map_palette(&red, &green, &blue, palette);
        }
        const size_t offset = (size_t)index * 4u;
        pixels[offset] = red;
        pixels[offset + 1u] = green;
        pixels[offset + 2u] = blue;
        pixels[offset + 3u] = (uint8_t)(pixel_alpha * brightness * 255.0);
    }
    Py_RETURN_NONE;
}

PyObject *render_comet_storm_into(PyObject *module, PyObject *args) {
    (void)module;
    PyArrayObject *destination;
    PyArrayObject *geometry;
    double brightness;
    int palette;
    unsigned int custom_rgb;
    if (!PyArg_ParseTuple(args, "O!O!diI", &PyArray_Type, &destination,
                          &PyArray_Type, &geometry, &brightness, &palette,
                          &custom_rgb))
        return NULL;
    if (validate_rgba(destination, "destination", 1) < 0)
        return NULL;
    const npy_intp geometry_shape[2] = {-1, 4};
    if (validate_array(geometry, NPY_DOUBLE, 2, geometry_shape, 0,
                       -INFINITY, INFINITY, "geometry") < 0 ||
        reject_any_overlap(destination, geometry, "destination", "geometry") < 0) {
        return NULL;
    }
    if (!isfinite(brightness) || brightness < 0.0 || brightness > 1.0 ||
        palette < 0 || palette > 3 || custom_rgb > 0xFFFFFFu) {
        PyErr_SetString(PyExc_ValueError, "invalid CometStorm render parameters");
        return NULL;
    }
    const double *comets = (const double *)PyArray_DATA(geometry);
    uint8_t *output = (uint8_t *)PyArray_DATA(destination);
    const npy_intp led_count = PyArray_DIM(destination, 0);
    const npy_intp comet_count = PyArray_DIM(geometry, 0);
    uint8_t clear_red = 0u, clear_green = 0u, clear_blue = 0u;
    if (palette == 3) {
        clear_red = (uint8_t)((custom_rgb >> 16) & 0xFFu);
        clear_green = (uint8_t)((custom_rgb >> 8) & 0xFFu);
        clear_blue = (uint8_t)(custom_rgb & 0xFFu);
    } else if (palette == 1 || palette == 2) {
        hsl_to_rgb(0.0, 0.0, 36.0, &clear_red, &clear_green, &clear_blue);
    }
    for (npy_intp index = 0; index < led_count; index++) {
        const size_t offset = (size_t)index * 4u;
        output[offset] = clear_red;
        output[offset + 1u] = clear_green;
        output[offset + 2u] = clear_blue;
        output[offset + 3u] = 0u;
    }
    for (npy_intp comet = 0; comet < comet_count; comet++) {
        const size_t base = (size_t)comet * 4u;
        const double head = comets[base];
        const double tail = fmax(comets[base + 1u], 0.001);
        const double hue = comets[base + 2u];
        const double alpha = comets[base + 3u];
        for (npy_intp index = 0; index < led_count; index++) {
            const double direct = fabs(head - (double)index);
            const double distance = fmin(direct, fmax((double)led_count - direct, 0.0));
            const double intensity = fmax(1.0 - distance / tail, 0.0);
            if (intensity <= 0.0)
                continue;
            const double strength = fmin(1.0, intensity * alpha);
            const size_t offset = (size_t)index * 4u;
            const double existing_alpha = (double)output[offset + 3u] / 255.0;
            if (strength < existing_alpha)
                continue;
            uint8_t red, green, blue;
            if (palette == 3) {
                red = (uint8_t)((custom_rgb >> 16) & 0xFFu);
                green = (uint8_t)((custom_rgb >> 8) & 0xFFu);
                blue = (uint8_t)(custom_rgb & 0xFFu);
            } else {
                hsl_to_rgb(hue, 100.0, 55.0, &red, &green, &blue);
                map_palette(&red, &green, &blue, palette);
            }
            output[offset] = red;
            output[offset + 1u] = green;
            output[offset + 2u] = blue;
            output[offset + 3u] = (uint8_t)(strength * brightness * 255.0);
        }
    }
    Py_RETURN_NONE;
}

PyObject *render_twinkle_into(PyObject *module, PyObject *args) {
    (void)module;
    PyArrayObject *destination, *levels, *hues;
    Py_ssize_t frame;
    int radius, palette;
    double bloom, brightness;
    unsigned int custom_rgb;
    if (!PyArg_ParseTuple(args, "O!O!O!niddiI", &PyArray_Type, &destination,
                          &PyArray_Type, &levels, &PyArray_Type, &hues,
                          &frame, &radius, &bloom, &brightness, &palette,
                          &custom_rgb))
        return NULL;
    if (validate_rgba(destination, "destination", 1) < 0)
        return NULL;
    const npy_intp count = PyArray_DIM(destination, 0);
    const npy_intp state_shape[1] = {count};
    if (validate_array(levels, NPY_FLOAT32, 1, state_shape, 0, 0.0, 1.0,
                       "levels") < 0 ||
        validate_array_layout(hues, NPY_INT16, 1, state_shape, 0, "hues") < 0 ||
        reject_any_overlap(destination, levels, "destination", "levels") < 0 ||
        reject_any_overlap(destination, hues, "destination", "hues") < 0 ||
        reject_any_overlap(levels, hues, "levels", "hues") < 0) {
        return NULL;
    }
    if (radius < 0 || !isfinite(bloom) || bloom < 0.0 || bloom > 1.0 ||
        !isfinite(brightness) ||
        brightness < 0.0 || brightness > 1.0 || palette < 0 || palette > 3 ||
        custom_rgb > 0xFFFFFFu) {
        PyErr_SetString(PyExc_ValueError, "invalid Twinkle render parameters");
        return NULL;
    }
    const float *level_values = (const float *)PyArray_DATA(levels);
    const int16_t *hue_values = (const int16_t *)PyArray_DATA(hues);
    uint8_t *output = (uint8_t *)PyArray_DATA(destination);
    const npy_intp radius_value = (npy_intp)radius;
    for (npy_intp index = 0; index < count; index++) {
        const double position = count <= 1 ? 0.0 : (double)index / (double)(count - 1);
        const double drift = sin((double)frame * 0.018 + position * 4.2) * 0.5 + 0.5;
        double level = (double)level_values[index];
        if (radius > 1) {
            const npy_intp left = index > radius_value ? index - radius_value : 0;
            const npy_intp remaining_right = count - index - 1;
            const npy_intp right = radius_value >= remaining_right
                ? count
                : index + radius_value + 1;
            for (npy_intp neighbor = left; neighbor < right; neighbor++) {
                const double neighbor_level = (double)level_values[neighbor] *
                    (1.0 - (double)llabs((long long)neighbor - (long long)index) /
                     ((double)radius + 1.0));
                if (neighbor_level > level)
                    level = neighbor_level;
            }
        }
        const double hue = level > 0.01
            ? (double)hue_values[index]
            : 154.0 + (double)(int)(drift * 8.0);
        const double alpha = fmin(0.05 + drift * 0.03 + level * 0.38 + bloom * 0.08, 0.5);
        const double lightness = 28.0 + (double)(int)(drift * 5.0 + level * 34.0);
        uint8_t red, green, blue;
        if (palette == 3) {
            red = (uint8_t)((custom_rgb >> 16) & 0xFFu);
            green = (uint8_t)((custom_rgb >> 8) & 0xFFu);
            blue = (uint8_t)(custom_rgb & 0xFFu);
        } else {
            hsl_to_rgb(hue, 65.0, lightness, &red, &green, &blue);
            map_palette(&red, &green, &blue, palette);
        }
        const size_t offset = (size_t)index * 4u;
        output[offset] = red;
        output[offset + 1u] = green;
        output[offset + 2u] = blue;
        volatile double scaled_alpha = alpha * brightness;
        output[offset + 3u] = (uint8_t)(scaled_alpha * 255.0);
    }
    Py_RETURN_NONE;
}

PyObject *render_comet_into(PyObject *module, PyObject *args) {
    (void)module;
    PyArrayObject *destination;
    double position, tail, shimmer, hue, intensity, brightness;
    int palette;
    unsigned int custom_rgb;
    if (!PyArg_ParseTuple(args, "O!ddddddiI", &PyArray_Type, &destination,
                          &position, &tail, &shimmer, &hue, &intensity,
                          &brightness, &palette, &custom_rgb))
        return NULL;
    if (validate_rgba(destination, "destination", 1) < 0)
        return NULL;
    if (!isfinite(position) || !isfinite(tail) || tail <= 0.0 ||
        !isfinite(shimmer) || shimmer < 0.0 || shimmer > 1.0 ||
        !isfinite(hue) || !isfinite(intensity) ||
        intensity < 0.0 || intensity > 1.0 || !isfinite(brightness) ||
        brightness < 0.0 || brightness > 1.0 || palette < 0 || palette > 3 ||
        custom_rgb > 0xFFFFFFu) {
        PyErr_SetString(PyExc_ValueError, "invalid Comet render parameters");
        return NULL;
    }
    static const int neon_hues[7] = {300, 160, 52, 206, 12, 268, 132};
    const npy_intp count = PyArray_DIM(destination, 0);
    uint8_t *pixels = (uint8_t *)PyArray_DATA(destination);
    for (npy_intp index = 0; index < count; index++) {
        const double strength = fmax(0.0, 1.0 - fabs(position - (double)index) / fmax(tail, 0.001));
        const size_t offset = (size_t)index * 4u;
        if (strength <= 0.0 || intensity <= 0.0) {
            pixels[offset] = pixels[offset + 1u] = pixels[offset + 2u] = 0u;
            pixels[offset + 3u] = 0u;
            continue;
        }
        const double alpha = fmin(1.0, (0.14 + strength * 0.86 + shimmer) * intensity);
        int lightness = 48 + (int)(strength * 12.0 + shimmer * 10.0);
        if (lightness > 64)
            lightness = 64;
        uint8_t red, green, blue;
        if (palette == 3) {
            red = (uint8_t)((custom_rgb >> 16u) & 0xFFu);
            green = (uint8_t)((custom_rgb >> 8u) & 0xFFu);
            blue = (uint8_t)(custom_rgb & 0xFFu);
        } else {
            double selected_hue = fmod(hue, 360.0);
            if (palette == 1) {
                double palette_hue = fmod(hue, 315.0);
                if (palette_hue < 0.0)
                    palette_hue += 315.0;
                selected_hue = (double)neon_hues[(int)(palette_hue / 45.0)];
            } else if (palette == 2) {
                static const int fire_hues[5] = {0, 10, 22, 35, 48};
                double palette_hue = fmod(hue, 275.0);
                if (palette_hue < 0.0)
                    palette_hue += 275.0;
                selected_hue = (double)fire_hues[(int)(palette_hue / 55.0)];
            }
            hsl_to_rgb(selected_hue, 100.0, (double)lightness,
                       &red, &green, &blue);
        }
        pixels[offset] = red;
        pixels[offset + 1u] = green;
        pixels[offset + 2u] = blue;
        pixels[offset + 3u] = (uint8_t)(alpha * brightness * 255.0);
    }
    Py_RETURN_NONE;
}

PyObject *render_disco_sparkle_into(PyObject *module, PyObject *args) {
    (void)module;
    PyArrayObject *destination, *intensities;
    long long frame;
    int audio_mode, density, accent_trigger, palette;
    double decay, variation, hue_rate, pulse, brightness;
    unsigned int custom_rgb;
    if (!PyArg_ParseTuple(args, "O!O!LiiidddddiI", &PyArray_Type, &destination,
                          &PyArray_Type, &intensities, &frame, &audio_mode,
                          &density, &accent_trigger, &decay, &variation,
                          &hue_rate, &pulse, &brightness, &palette, &custom_rgb))
        return NULL;
    if (validate_rgba(destination, "destination", 1) < 0)
        return NULL;
    const npy_intp intensity_shape[1] = {PyArray_DIM(destination, 0)};
    if (validate_array(intensities, NPY_FLOAT32, 1, intensity_shape, 1,
                       0.0, 1.0, "intensities") < 0 ||
        reject_any_overlap(destination, intensities, "destination", "intensities") < 0) {
        return NULL;
    }
    if (frame < 0 || (audio_mode != 0 && audio_mode != 1) ||
        density < 0 || density > 100 ||
        (accent_trigger != 0 && accent_trigger != 1) ||
        !isfinite(decay) || decay < 0.0 || decay > 1.0 ||
        !isfinite(variation) || variation < 0.0 || variation > 1.0 ||
        !isfinite(hue_rate) || hue_rate < 0.0 || hue_rate > 3.0 ||
        !isfinite(pulse) || pulse < 0.0 || pulse > 10.0 ||
        !isfinite(brightness) || brightness < 0.0 || brightness > 1.0 ||
        palette < 0 || palette > 3 || custom_rgb > 0xFFFFFFu) {
        PyErr_SetString(PyExc_ValueError, "invalid DiscoSparkle render parameters");
        return NULL;
    }
    const npy_intp count = PyArray_DIM(destination, 0);
    float *state = (float *)PyArray_DATA(intensities);
    uint8_t *pixels = (uint8_t *)PyArray_DATA(destination);
    const uint64_t frame_seed = (uint64_t)frame * UINT64_C(1103515245);
    const float decay_f = (float)decay;
    for (npy_intp index = 0; index < count; index++) {
        float strength = state[index] * decay_f;
        state[index] = strength;
        const uint64_t seed = frame_seed + (uint64_t)index * UINT64_C(12345);
        const uint64_t trigger_value = seed % UINT64_C(100);
        const int trigger = audio_mode
            ? (accent_trigger || trigger_value < (uint64_t)density)
            : trigger_value < (uint64_t)density;
        if (trigger) {
            const double increment = audio_mode ? 0.8 : 0.7;
            strength = (float)fmin((double)strength + increment, 1.0);
            state[index] = strength;
        }
        const size_t offset = (size_t)index * 4u;
        if (strength <= 0.05f) {
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
        const int variation_value =
            (int)(((int)(seed % UINT64_C(21)) - 10) * variation);
        const int animated_frame = audio_mode
            ? (int)fmod(trunc((double)frame * hue_rate), 256.0)
            : (int)(((frame % 256LL) * 3LL) % 256LL);
        int hue = (int)((seed / UINT64_C(100)) % UINT64_C(256)) + animated_frame + variation_value;
        hue %= 256;
        if (hue < 0)
            hue += 256;
        int lightness = audio_mode
            ? (int)(40.0 + (double)strength * 25.0 + pulse)
            : 50;
        uint8_t red, green, blue;
        palette_hsl_to_rgb((double)hue, 100.0, (double)lightness,
                           palette, custom_rgb, &red, &green, &blue);
        pixels[offset] = red;
        pixels[offset + 1u] = green;
        pixels[offset + 2u] = blue;
        pixels[offset + 3u] = (uint8_t)(brightness * 255.0);
    }
    Py_RETURN_NONE;
}

void write_transparent_palette_pixel(uint8_t *pixels, size_t offset,
                                            int palette,
                                            unsigned int custom_rgb) {
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
}

PyObject *render_firework_burst_into(PyObject *module, PyObject *args) {
    (void)module;
    PyArrayObject *destination, *phases, *bursts;
    int phase_count, burst_count, audio_mode, hue, palette;
    long long frame;
    double center, fade_alpha, glow, mid, high, brightness;
    PyObject *bands_object;
    unsigned int custom_rgb;
    if (!PyArg_ParseTuple(args, "O!O!O!iiLiddddddOiiI", &PyArray_Type,
                          &destination, &PyArray_Type, &phases, &PyArray_Type,
                          &bursts, &phase_count, &burst_count, &frame,
                          &audio_mode, &center, &fade_alpha, &glow, &mid,
                          &high, &brightness, &bands_object, &hue, &palette,
                          &custom_rgb))
        return NULL;
    if (validate_rgba(destination, "destination", 1) < 0)
        return NULL;
    if (phase_count < 0 || phase_count > 12 || burst_count < 0 || burst_count > 12) {
        PyErr_SetString(PyExc_ValueError, "FireworkBurst counts must be between zero and twelve");
        return NULL;
    }
    const npy_intp phase_shape[1] = {12};
    const npy_intp burst_shape[2] = {12, 3};
    if (validate_array_layout(phases, NPY_FLOAT64, 1, phase_shape, 0,
                              "phases") < 0 ||
        validate_array_layout(bursts, NPY_FLOAT64, 2, burst_shape, 0,
                              "bursts") < 0 ||
        validate_finite_prefix(phases, phase_count, "phases") < 0 ||
        validate_finite_prefix(bursts, (npy_intp)burst_count * 3,
                               "bursts") < 0 ||
        reject_any_overlap(destination, phases, "destination", "phases") < 0 ||
        reject_any_overlap(destination, bursts, "destination", "bursts") < 0 ||
        reject_any_overlap(phases, bursts, "phases", "bursts") < 0) {
        return NULL;
    }
    const double *phase_data = (const double *)PyArray_DATA(phases);
    const double *burst_data = (const double *)PyArray_DATA(bursts);
    for (int i = 0; i < phase_count; i++) {
        if (phase_data[i] < 0.0 || phase_data[i] > (double)INT_MAX / 10.0) {
            PyErr_SetString(PyExc_ValueError, "active phases are outside the supported range");
            return NULL;
        }
    }
    for (int i = 0; i < burst_count; i++) {
        if (burst_data[i * 3] < 0.0 ||
            burst_data[i * 3] > (double)INT_MAX / 10.0 ||
            burst_data[i * 3 + 1] < 0.0 || burst_data[i * 3 + 1] > 1.0 ||
            burst_data[i * 3 + 2] < 0.0 || burst_data[i * 3 + 2] > 255.0) {
            PyErr_SetString(PyExc_ValueError, "active burst geometry is outside the supported range");
            return NULL;
        }
    }
    if (phase_count < 0 || phase_count > 12 || burst_count < 0 || burst_count > 12 ||
        (audio_mode != 0 && audio_mode != 1) || !isfinite(center) || center < 0.0 ||
        !isfinite(fade_alpha) || fade_alpha < 0.0 || fade_alpha > 1.0 ||
        !isfinite(glow) || glow < 0.0 || glow > 1.0 ||
        !isfinite(mid) || mid < 0.0 || mid > 1.0 ||
        !isfinite(high) || high < 0.0 || high > 1.0 || frame < 0 ||
        !isfinite(brightness) || brightness < 0.0 || brightness > 1.0 ||
        hue < 0 || hue > 255 || palette < 0 || palette > 3 ||
        custom_rgb > 0xFFFFFFu) {
        PyErr_SetString(PyExc_ValueError, "invalid FireworkBurst render parameters");
        return NULL;
    }
    const int burst_cycle_index = (int)((frame / 36LL) % 5LL);
    double band_values[32] = {0.0};
    Py_ssize_t band_count = 0;
    if (bands_object != Py_None) {
        PyObject *sequence = PySequence_Fast(bands_object, "bands must be a sequence");
        if (sequence == NULL)
            return NULL;
        band_count = PySequence_Fast_GET_SIZE(sequence);
        if (band_count > 32) {
            Py_DECREF(sequence);
            PyErr_SetString(PyExc_ValueError, "FireworkBurst supports at most 32 bands");
            return NULL;
        }
        for (Py_ssize_t band = 0; band < band_count; band++) {
            band_values[band] = PyFloat_AsDouble(PySequence_Fast_GET_ITEM(sequence, band));
            if (PyErr_Occurred() || !isfinite(band_values[band]) ||
                band_values[band] < 0.0 || band_values[band] > 1.0) {
                Py_DECREF(sequence);
                if (!PyErr_Occurred())
                    PyErr_SetString(PyExc_ValueError,
                                    "bands must be normalized finite values");
                return NULL;
            }
        }
        Py_DECREF(sequence);
    }

    const double *phase_values = (const double *)PyArray_DATA(phases);
    const double *burst_values = (const double *)PyArray_DATA(bursts);
    const npy_intp length = PyArray_DIM(destination, 0);
    uint8_t *pixels = (uint8_t *)PyArray_DATA(destination);
    for (npy_intp index = 0; index < length; index++) {
        const double dist = fabs((double)index - center);
        double alpha = 0.0;
        int pixel_hue = hue;
        int lightness = 60;
        if (!audio_mode) {
            for (int phase_index = 0; phase_index < phase_count; phase_index++) {
                const double phase = phase_values[phase_index];
                if (dist <= phase) {
                    const double sparkle_dist = (phase - dist) / fmax(phase, 1.0);
                    const double candidate = fmax(1.0 - sparkle_dist * 1.5, 0.0);
                    const int offset = ((int)(index % 5) * 7 +
                                        burst_cycle_index * 13) % 5;
                    const double variance = (double)(offset - 2) * 0.04;
                    alpha = fmax(alpha, fmax(candidate + variance, 0.0));
                }
            }
            if (alpha <= 0.0) {
                write_transparent_palette_pixel(pixels, (size_t)index * 4u,
                                                palette, custom_rgb);
                continue;
            }
            alpha *= 0.85 * fade_alpha;
        } else {
            alpha = glow;
            for (int burst_index = 0; burst_index < burst_count; burst_index++) {
                const double phase = burst_values[burst_index * 3];
                const double strength = burst_values[burst_index * 3 + 1];
                const int burst_hue = (int)burst_values[burst_index * 3 + 2];
                if (dist <= phase) {
                    const double sparkle_dist = (phase - dist) / fmax(phase, 1.0);
                    double wave_alpha = fmax(1.0 - sparkle_dist * 1.8, 0.0) * strength;
                    const int phase_units = (int)(phase * 10.0);
                    const int particle =
                        ((int)(index % 7) * 7 + (phase_units % 7) * 13) % 7;
                    wave_alpha = fmax(wave_alpha + (double)(particle - 3) * 0.06, 0.0);
                    double band_sparkle = 0.0;
                    if (band_count > 0) {
                        const double scaled_band_index =
                            (double)index * (double)band_count / (double)length;
                        const Py_ssize_t band_index = (Py_ssize_t)fmin(
                            scaled_band_index, (double)(band_count - 1));
                        band_sparkle = band_values[band_index] * 0.2;
                    }
                    alpha = fmin(alpha + wave_alpha + band_sparkle, 1.0);
                    if (wave_alpha > 0.0)
                        pixel_hue = burst_hue;
                }
            }
            if (alpha <= 0.0) {
                write_transparent_palette_pixel(pixels, (size_t)index * 4u,
                                                palette, custom_rgb);
                continue;
            }
            lightness = (int)(45.0 + mid * 20.0 + high * 10.0);
        }
        uint8_t red, green, blue;
        palette_hsl_to_rgb((double)pixel_hue, 100.0, (double)lightness,
                           palette, custom_rgb, &red, &green, &blue);
        const size_t output_offset = (size_t)index * 4u;
        pixels[output_offset] = red;
        pixels[output_offset + 1u] = green;
        pixels[output_offset + 2u] = blue;
        pixels[output_offset + 3u] = (uint8_t)(alpha * brightness * 255.0);
    }
    Py_RETURN_NONE;
}

PyObject *render_color_burst_into(PyObject *module, PyObject *args) {
    (void)module;
    PyArrayObject *destination, *bursts;
    int burst_count, palette;
    double brightness;
    unsigned int custom_rgb;
    if (!PyArg_ParseTuple(args, "O!O!iidI", &PyArray_Type, &destination,
                          &PyArray_Type, &bursts, &burst_count, &palette,
                          &brightness, &custom_rgb))
        return NULL;
    if (validate_rgba(destination, "destination", 1) < 0)
        return NULL;
    if (burst_count < 0 || burst_count > 6) {
        PyErr_SetString(PyExc_ValueError, "ColorBurst count must be between zero and six");
        return NULL;
    }
    const npy_intp burst_shape[2] = {6, 5};
    if (validate_array_layout(bursts, NPY_FLOAT64, 2, burst_shape, 0,
                              "bursts") < 0 ||
        validate_finite_prefix(bursts, (npy_intp)burst_count * 5,
                               "bursts") < 0 ||
        reject_any_overlap(destination, bursts, "destination", "bursts") < 0) {
        return NULL;
    }
    if (burst_count < 0 || burst_count > 6 || !isfinite(brightness) ||
        brightness < 0.0 || brightness > 1.0 || palette < 0 || palette > 3 ||
        custom_rgb > 0xFFFFFFu) {
        PyErr_SetString(PyExc_ValueError, "invalid ColorBurst render parameters");
        return NULL;
    }
    const double *state = (const double *)PyArray_DATA(bursts);
    for (int burst_index = 0; burst_index < burst_count; burst_index++) {
        const double hue = state[burst_index * 5 + 2];
        const double strength = state[burst_index * 5 + 3];
        if (hue < (double)INT_MIN || hue > (double)INT_MAX ||
            strength < 0.0 || strength > 1.0) {
            PyErr_SetString(PyExc_ValueError,
                            "active burst hue or strength is outside the supported range");
            return NULL;
        }
    }
    const npy_intp length = PyArray_DIM(destination, 0);
    uint8_t *pixels = (uint8_t *)PyArray_DATA(destination);
    for (npy_intp index = 0; index < length; index++) {
        const size_t offset = (size_t)index * 4u;
        pixels[offset] = pixels[offset + 1u] = pixels[offset + 2u] = 0u;
        pixels[offset + 3u] = 255u;
    }
    static const int club_hues[7] = {330, 18, 48, 126, 190, 224, 286};
    for (int burst_index = 0; burst_index < burst_count; burst_index++) {
        const double center = state[burst_index * 5];
        const double radius = state[burst_index * 5 + 1];
        const int hue_seed = (int)state[burst_index * 5 + 2];
        const double strength = state[burst_index * 5 + 3];
        const double width = fmax(state[burst_index * 5 + 4], 0.001);
        const double base_alpha = fmin(1.0, 0.18 + strength * 0.82);
        for (npy_intp index = 0; index < length; index++) {
            const double distance = fabs((double)index - center);
            const double intensity = fmax(0.0, 1.0 - fabs(distance - radius) / width);
            if (intensity <= 0.0)
                continue;
            int hue_index = (hue_seed % 7 + (int)(index % 7)) % 7;
            if (hue_index < 0)
                hue_index += 7;
            uint8_t red, green, blue;
            palette_hsl_to_rgb((double)club_hues[hue_index], 100.0, 58.0,
                               palette, custom_rgb, &red, &green, &blue);
            const size_t offset = (size_t)index * 4u;
            pixels[offset] = red;
            pixels[offset + 1u] = green;
            pixels[offset + 2u] = blue;
            pixels[offset + 3u] = (uint8_t)(fmin(1.0, intensity * base_alpha) *
                                             brightness * 255.0);
        }
    }
    Py_RETURN_NONE;
}

PyObject *render_neon_confetti_into(PyObject *module, PyObject *args) {
    (void)module;
    PyArrayObject *destination, *levels, *hues;
    Py_ssize_t frame;
    double fade_multiplier, spawn_threshold, high, spread, brightness;
    int palette;
    unsigned int custom_rgb;
    if (!PyArg_ParseTuple(args, "O!O!O!ndddddiI", &PyArray_Type, &destination,
                          &PyArray_Type, &levels, &PyArray_Type, &hues, &frame,
                          &fade_multiplier, &spawn_threshold, &high, &spread,
                          &brightness, &palette, &custom_rgb))
        return NULL;
    if (validate_rgba(destination, "destination", 1) < 0)
        return NULL;
    const npy_intp length = PyArray_DIM(destination, 0);
    const npy_intp state_shape[1] = {length};
    if (validate_array(levels, NPY_FLOAT32, 1, state_shape, 1, 0.0, 1.0,
                       "levels") < 0 ||
        validate_int16_array_range(hues, 1, state_shape, 1, 0, 359, "hues") < 0 ||
        reject_any_overlap(destination, levels, "destination", "levels") < 0 ||
        reject_any_overlap(destination, hues, "destination", "hues") < 0 ||
        reject_any_overlap(levels, hues, "levels", "hues") < 0) {
        return NULL;
    }
    if (!isfinite(fade_multiplier) || fade_multiplier < 0.0 || fade_multiplier > 1.0 ||
        !isfinite(spawn_threshold) || spawn_threshold < 0.0 ||
        !isfinite(high) || high < 0.0 || high > 1.0 || !isfinite(spread) ||
        spread < 0.0 || spread > 1.0 || !isfinite(brightness) ||
        brightness < 0.0 || brightness > 1.0 || palette < 0 || palette > 3 ||
        custom_rgb > 0xFFFFFFu) {
        PyErr_SetString(PyExc_ValueError, "invalid NeonConfetti render parameters");
        return NULL;
    }
    float *level_values = (float *)PyArray_DATA(levels);
    int16_t *hue_values = (int16_t *)PyArray_DATA(hues);
    uint8_t *pixels = (uint8_t *)PyArray_DATA(destination);
    const npy_intp safe_length = length > 0 ? length : 1;
    const double center = (double)multiply_modulo_ssize(frame, 53u, safe_length);
    for (npy_intp index = 0; index < length; index++) {
        const size_t offset = (size_t)index * 4u;
        pixels[offset] = pixels[offset + 1u] = pixels[offset + 2u] = 0u;
        pixels[offset + 3u] = 255u;
        level_values[index] = (float)((double)level_values[index] * fade_multiplier);
        const uint32_t seed = (uint32_t)((uint64_t)frame * 48271u +
                                         (uint64_t)index * 69621u);
        const double direct_distance = fabs((double)index - center);
        const double distance = fmin(direct_distance, (double)length - direct_distance);
        const double weight = (1.0 - spread) + spread *
            fmax(0.0, 1.0 - distance / fmax((double)length * 0.5, 1.0));
        if ((double)(seed % 10000u) < spawn_threshold * weight) {
            level_values[index] = (float)fmin(
                1.0, (double)level_values[index] + 0.5 + high * 0.25);
            hue_values[index] = (int16_t)(((seed / 100u) +
                                           (uint32_t)index * 13u) % 360u);
        }
        const double level = (double)level_values[index];
        if (level <= 0.015)
            continue;
        const int hue_index = (int)hue_values[index] % 7;
        static const int neon_hues[7] = {300, 160, 52, 206, 12, 268, 132};
        uint8_t red, green, blue;
        palette_hsl_to_rgb((double)neon_hues[hue_index], 100.0, 64.0,
                           palette, custom_rgb, &red, &green, &blue);
        double alpha = fmin(1.0, 0.12 + level * 0.78) * brightness;
        if (alpha > 1.0)
            alpha = 1.0;
        pixels[offset] = red;
        pixels[offset + 1u] = green;
        pixels[offset + 2u] = blue;
        pixels[offset + 3u] = (uint8_t)(alpha * 255.0);
    }
    Py_RETURN_NONE;
}

PyObject *render_disco_comet_into(PyObject *module, PyObject *args) {
    (void)module;
    PyArrayObject *destination;
    Py_ssize_t frame;
    int hue, sparkle_threshold, palette;
    double phase, width, trail, high, brightness;
    unsigned int custom_rgb;
    if (!PyArg_ParseTuple(args, "O!dddidindiI", &PyArray_Type, &destination,
                          &phase, &width, &trail, &hue, &high,
                          &sparkle_threshold, &frame, &brightness, &palette,
                          &custom_rgb))
        return NULL;
    if (validate_rgba(destination, "destination", 1) < 0)
        return NULL;
    if (!isfinite(phase) || !isfinite(width) || width <= 0.0 ||
        !isfinite(trail) || trail < 0.0 || trail > 1.0 || hue < 0 || hue > 255 ||
        !isfinite(high) || high < 0.0 || high > 1.0 || sparkle_threshold < 0 ||
        sparkle_threshold > 100 || !isfinite(brightness) || brightness < 0.0 ||
        brightness > 1.0 || palette < 0 || palette > 3 || custom_rgb > 0xFFFFFFu) {
        PyErr_SetString(PyExc_ValueError, "invalid DiscoComet render parameters");
        return NULL;
    }
    static const int club_hues[7] = {330, 18, 48, 126, 190, 224, 286};
    static const int neon_hues[7] = {300, 160, 52, 206, 12, 268, 132};
    const npy_intp length = PyArray_DIM(destination, 0);
    uint8_t *pixels = (uint8_t *)PyArray_DATA(destination);
    for (npy_intp index = 0; index < length; index++) {
        const size_t offset = (size_t)index * 4u;
        pixels[offset] = pixels[offset + 1u] = pixels[offset + 2u] = 0u;
        pixels[offset + 3u] = 255u;
    }
    for (npy_intp index = 0; index < length; index++) {
        const double distance = fabs((double)index - phase);
        if (distance > width)
            continue;
        const double intensity = fmax(0.0, 1.0 - distance / width);
        uint8_t red, green, blue;
        const int club_index =
            (hue + (int)((index % 7) * 5)) % 7;
        palette_hsl_to_rgb((double)club_hues[club_index], 100.0, 58.0,
                           palette, custom_rgb, &red, &green, &blue);
        double alpha = fmin(1.0, trail + intensity * 0.85) * brightness;
        if (alpha > 1.0)
            alpha = 1.0;
        size_t offset = (size_t)index * 4u;
        pixels[offset] = red;
        pixels[offset + 1u] = green;
        pixels[offset + 2u] = blue;
        pixels[offset + 3u] = (uint8_t)(alpha * 255.0);
        const uint64_t sparkle_seed = (uint64_t)frame * 97u +
            (uint64_t)index * 31u + (uint64_t)(int)(high * 100.0);
        if (distance < width * 0.8 &&
            (int)(sparkle_seed % 100u) < sparkle_threshold) {
            const int seed_modulo = (int)(sparkle_seed % 7u);
            const int neon_index = (hue + seed_modulo) % 7;
            const double sparkle_alpha = fmin(1.0, 0.65 + high * 0.35);
            palette_hsl_to_rgb((double)neon_hues[neon_index], 100.0, 62.0,
                               palette, custom_rgb, &red, &green, &blue);
            offset = (size_t)index * 4u;
            pixels[offset] = red;
            pixels[offset + 1u] = green;
            pixels[offset + 2u] = blue;
            pixels[offset + 3u] = (uint8_t)(sparkle_alpha * brightness * 255.0);
            if (index + 1 < length && (sparkle_seed / 7u) % 2u == 0u) {
                const int neighbor_index = (hue + seed_modulo + 13) % 7;
                const double neighbor_alpha = fmin(1.0, 0.42 + high * 0.24);
                palette_hsl_to_rgb((double)neon_hues[neighbor_index], 100.0, 62.0,
                                   palette, custom_rgb, &red, &green, &blue);
                offset = (size_t)(index + 1) * 4u;
                pixels[offset] = red;
                pixels[offset + 1u] = green;
                pixels[offset + 2u] = blue;
                pixels[offset + 3u] = (uint8_t)(neighbor_alpha * brightness * 255.0);
            }
        }
    }
    Py_RETURN_NONE;
}

PyObject *render_pixel_explosion_into(PyObject *module, PyObject *args) {
    (void)module;
    PyArrayObject *destination, *bursts;
    int burst_count, palette;
    double width, brightness;
    unsigned int custom_rgb;
    if (!PyArg_ParseTuple(args, "O!O!iddiI", &PyArray_Type, &destination,
                          &PyArray_Type, &bursts, &burst_count, &width,
                          &brightness, &palette, &custom_rgb))
        return NULL;
    if (validate_rgba(destination, "destination", 1) < 0)
        return NULL;
    const npy_intp length = PyArray_DIM(destination, 0);
    if (PyArray_NDIM(bursts) != 2 || PyArray_DIM(bursts, 1) != 4 ||
        burst_count < 0 || burst_count > PyArray_DIM(bursts, 0)) {
        PyErr_SetString(PyExc_ValueError,
                        "PixelExplosion bursts must be contiguous float64 rows");
        return NULL;
    }
    const npy_intp burst_shape[2] = {-1, 4};
    if (validate_array_layout(bursts, NPY_FLOAT64, 2, burst_shape, 0,
                              "bursts") < 0 ||
        validate_finite_prefix(bursts, (npy_intp)burst_count * 4,
                               "bursts") < 0 ||
        reject_any_overlap(destination, bursts, "destination", "bursts") < 0)
        return NULL;
    if (!isfinite(width) || width <= 0.0 || !isfinite(brightness) ||
        brightness < 0.0 || brightness > 1.0 || palette < 0 || palette > 3 ||
        custom_rgb > 0xFFFFFFu) {
        PyErr_SetString(PyExc_ValueError, "invalid PixelExplosion render parameters");
        return NULL;
    }
    const double *state = (const double *)PyArray_DATA(bursts);
    for (int burst = 0; burst < burst_count; burst++) {
        const double hue = state[burst * 4 + 2];
        const double alpha = state[burst * 4 + 3];
        if (hue < 0.0 || hue > 359.0 || alpha < 0.0 || alpha > 1.0) {
            PyErr_SetString(PyExc_ValueError,
                            "active explosion hues or alpha are outside the supported range");
            return NULL;
        }
    }
    uint8_t *pixels = (uint8_t *)PyArray_DATA(destination);
    for (npy_intp index = 0; index < length; index++) {
        const size_t offset = (size_t)index * 4u;
        pixels[offset] = pixels[offset + 1u] = pixels[offset + 2u] = 0u;
        pixels[offset + 3u] = 0u;
    }
    for (int burst = 0; burst < burst_count; burst++) {
        const double x = state[burst * 4];
        const double radius = state[burst * 4 + 1];
        const int hue = (int)state[burst * 4 + 2];
        const double burst_alpha = state[burst * 4 + 3];
        for (npy_intp index = 0; index < length; index++) {
            const double distance = fabs((double)index - x);
            const double ring = fmax(1.0 - fabs(distance - radius) / width, 0.0);
            const double alpha = ring * burst_alpha;
            if (ring <= 0.0 || alpha <= 0.01)
                continue;
            uint8_t red, green, blue;
            palette_hsl_to_rgb((double)hue, 100.0, 55.0, palette, custom_rgb,
                               &red, &green, &blue);
            const size_t offset = (size_t)index * 4u;
            pixels[offset] = red;
            pixels[offset + 1u] = green;
            pixels[offset + 2u] = blue;
            pixels[offset + 3u] = (uint8_t)(alpha * brightness * 255.0);
        }
    }
    Py_RETURN_NONE;
}

static PyMethodDef methods[] = {
    {"render_confetti_into", render_confetti_into, METH_VARARGS,
     "Advance Confetti intensity state and render particles into an RGBA frame."},
    {"render_glow_rush_into", render_glow_rush_into, METH_VARARGS,
     "Render GlowRush tail and glow updates without clearing untouched pixels."},
    {"render_dual_comet_into", render_dual_comet_into, METH_VARARGS,
     "Render overlapping DualComet trails into an RGBA frame."},
    {"render_comet_storm_into", render_comet_storm_into, METH_VARARGS,
     "Render overlapping CometStorm trails into an RGBA frame."},
    {"render_twinkle_into", render_twinkle_into, METH_VARARGS,
     "Render the Twinkle state buffer into an RGBA frame."},
    {"render_comet_into", render_comet_into, METH_VARARGS,
     "Render a Comet trail into an RGBA frame."},
    {"render_disco_sparkle_into", render_disco_sparkle_into, METH_VARARGS,
     "Advance DiscoSparkle intensity state and render RGBA pixels."},
    {"render_firework_burst_into", render_firework_burst_into, METH_VARARGS,
     "Render static FireworkBurst phases or active audio bursts."},
    {"render_color_burst_into", render_color_burst_into, METH_VARARGS,
     "Render up to six ColorBurst rings over an opaque black frame."},
    {"render_neon_confetti_into", render_neon_confetti_into, METH_VARARGS,
     "Advance and render NeonConfetti's deterministic particle field."},
    {"render_disco_comet_into", render_disco_comet_into, METH_VARARGS,
     "Render DiscoComet's moving trail and deterministic sparkle overlay."},
    {"render_pixel_explosion_into", render_pixel_explosion_into, METH_VARARGS,
     "Render active PixelExplosion rings into a transparent RGBA frame."},
    {NULL, NULL, 0, NULL}
};

PyMethodDef *native_particles_methods(void) { return methods; }
