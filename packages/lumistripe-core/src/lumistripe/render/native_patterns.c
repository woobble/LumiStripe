#include "native_common.h"
PyObject *render_tunnel_rings_into(PyObject *module, PyObject *args) {
    (void)module;
    PyArrayObject *destination;
    PyArrayObject *geometry;
    PyArrayObject *colors;
    PyArrayObject *clear_color;
    int ring_count;
    double brightness;
    if (!PyArg_ParseTuple(args, "O!O!O!O!id", &PyArray_Type, &destination,
                          &PyArray_Type, &geometry, &PyArray_Type, &colors,
                          &PyArray_Type, &clear_color, &ring_count, &brightness)) {
        return NULL;
    }
    if (validate_rgba(destination, "destination", 1) < 0 ||
        validate_fixed_array(geometry, NPY_DOUBLE, 2, 2, 5, "geometry") < 0 ||
        validate_fixed_array(colors, NPY_UINT8, 2, 2, 3, "colors") < 0 ||
        validate_fixed_array(clear_color, NPY_UINT8, 1, 3, 0, "clear_color") < 0) {
        return NULL;
    }
    if (ring_count < 1 || ring_count > 2) {
        PyErr_SetString(PyExc_ValueError, "ring_count must be one or two");
        return NULL;
    }
    if (!isfinite(brightness) || brightness < 0.0 || brightness > 1.0) {
        PyErr_SetString(PyExc_ValueError, "brightness must be finite and between zero and one");
        return NULL;
    }

    const double *ring_data = (const double *)PyArray_DATA(geometry);
    const uint8_t *ring_colors = (const uint8_t *)PyArray_DATA(colors);
    const uint8_t *background = (const uint8_t *)PyArray_DATA(clear_color);
    uint8_t *output = (uint8_t *)PyArray_DATA(destination);
    const npy_intp led_count = PyArray_DIM(destination, 0);

    for (int ring_index = 0; ring_index < ring_count; ring_index++) {
        const size_t parameter_offset = (size_t)ring_index * 5u;
        const double center = ring_data[parameter_offset];
        const double radius = ring_data[parameter_offset + 1u];
        const double width = ring_data[parameter_offset + 2u];
        const double max_distance = ring_data[parameter_offset + 3u];
        const double alpha = ring_data[parameter_offset + 4u];
        if (!isfinite(center) || !isfinite(radius) || !isfinite(width) ||
            !isfinite(max_distance) || !isfinite(alpha) || width <= 0.0 ||
            max_distance <= 0.0) {
            PyErr_SetString(PyExc_ValueError, "ring geometry must be finite with positive width and distance");
            return NULL;
        }
    }

    for (npy_intp index = 0; index < led_count; index++) {
        const size_t offset = (size_t)index * 4u;
        output[offset] = background[0];
        output[offset + 1u] = background[1];
        output[offset + 2u] = background[2];
        output[offset + 3u] = 0u;
    }

    for (int ring_index = 0; ring_index < ring_count; ring_index++) {
        const size_t parameter_offset = (size_t)ring_index * 5u;
        const double center = ring_data[parameter_offset];
        const double radius = ring_data[parameter_offset + 1u];
        const double width = ring_data[parameter_offset + 2u];
        const double max_distance = ring_data[parameter_offset + 3u];
        const double alpha = ring_data[parameter_offset + 4u];
        const size_t color_offset = (size_t)ring_index * 3u;

        for (npy_intp index = 0; index < led_count; index++) {
            const double distance = fabs((double)index - center);
            const double ring = fmax(1.0 - fabs(distance - radius) / width, 0.0);
            const double ambient = fmax(1.0 - distance / max_distance, 0.0) * 0.1;
            double combined = fmin(ambient + ring * alpha, 1.0);
            if (combined <= 0.0)
                continue;

            const size_t offset = (size_t)index * 4u;
            const uint8_t previous_alpha = output[offset + 3u];
            if (previous_alpha > 0u) {
                const double visible_alpha = (double)previous_alpha / 255.0;
                if (visible_alpha > combined)
                    combined = visible_alpha;
            }
            output[offset] = ring_colors[color_offset];
            output[offset + 1u] = ring_colors[color_offset + 1u];
            output[offset + 2u] = ring_colors[color_offset + 2u];
            double scaled_alpha = combined * brightness;
            if (scaled_alpha > 1.0)
                scaled_alpha = 1.0;
            output[offset + 3u] = (uint8_t)(scaled_alpha * 255.0);
        }
    }
    Py_RETURN_NONE;
}

PyObject *render_rainbow_pattern_into(PyObject *module, PyObject *args) {
    (void)module;
    PyArrayObject *destination;
    PyObject *bands_object;
    int mode, palette;
    Py_ssize_t frame;
    double spread, base_hue, saturation, alpha, low, mid, beat_pulse, brightness;
    unsigned int custom_rgb;
    if (!PyArg_ParseTuple(args, "O!inOddddddddiI", &PyArray_Type, &destination,
                          &mode, &frame, &bands_object, &spread, &base_hue,
                          &saturation, &alpha, &low, &mid, &beat_pulse,
                          &brightness, &palette, &custom_rgb))
        return NULL;
    if (validate_rgba(destination, "destination", 1) < 0)
        return NULL;
    if (mode < 0 || mode > 2 || !isfinite(spread) || spread <= 0.0 ||
        !isfinite(base_hue) || !isfinite(saturation) || !isfinite(alpha) ||
        !isfinite(low) || !isfinite(mid) || !isfinite(beat_pulse) ||
        !isfinite(brightness) || alpha < 0.0 || alpha > 1.0 ||
        brightness < 0.0 || brightness > 1.0 || palette < 0 || palette > 3 ||
        custom_rgb > 0xFFFFFFu) {
        PyErr_SetString(PyExc_ValueError, "invalid rainbow render parameters");
        return NULL;
    }
    double band_values[8] = {0.0};
    if (bands_object != Py_None) {
        PyObject *bands = PySequence_Fast(bands_object, "bands must be a sequence or None");
        if (bands == NULL)
            return NULL;
        if (PySequence_Fast_GET_SIZE(bands) != 8) {
            Py_DECREF(bands);
            PyErr_SetString(PyExc_ValueError, "bands must contain exactly eight values");
            return NULL;
        }
        for (Py_ssize_t index = 0; index < 8; index++) {
            band_values[index] = PyFloat_AsDouble(PySequence_Fast_GET_ITEM(bands, index));
            if (PyErr_Occurred() || !isfinite(band_values[index])) {
                Py_DECREF(bands);
                if (!PyErr_Occurred())
                    PyErr_SetString(PyExc_ValueError, "bands must be finite values");
                return NULL;
            }
        }
        Py_DECREF(bands);
    }
    const int audio_mode = bands_object != Py_None;
    const npy_intp count = PyArray_DIM(destination, 0);
    uint8_t *pixels = (uint8_t *)PyArray_DATA(destination);
    const uint8_t output_alpha = (uint8_t)(alpha * brightness * 255.0);
    for (npy_intp index = 0; index < count; index++) {
        const double position = count <= 1 ? 0.0 : (double)index / (double)(count - 1);
        double local = 0.0;
        if (audio_mode) {
            if (count <= 1) {
                local = band_values[0];
            } else {
                const double band_position = (double)index * 8.0 / (double)(count - 1);
                const int center = (int)band_position;
                const int start = center > 0 ? center - 1 : 0;
                const int end = center + 2 < 8 ? center + 2 : 8;
                double total = 0.0;
                int band_count = 0;
                for (int band = start; band < end; band++) {
                    total += band_values[band];
                    band_count++;
                }
                local = band_count > 0 ? total / (double)band_count : 0.0;
            }
        }
        int hue, lightness;
        if (mode == 2) {
            hue = (int)base_hue + (int)((double)index * spread);
            lightness = (int)mid;
        } else if (mode == 0) {
            if (!audio_mode) {
                hue = (int)(position * 256.0 * spread + (double)frame * 1.5);
                lightness = 50;
            } else {
                hue = (int)(position * 256.0 * spread) + (int)base_hue + (int)(local * 44.0);
                lightness = 48 + (int)(mid * 6.0 + local * 6.0);
            }
        } else if (!audio_mode) {
            const int wave = (int)(sin((double)frame * 0.035) * 10.0);
            hue = (int)(frame % 256) + (int)(position * 256.0 * spread) + wave;
            lightness = 48 + (int)((sin((double)frame * 0.06 + position * 6.0) * 0.5 + 0.5) * 4.0);
        } else {
            hue = (int)base_hue + (int)(position * 256.0 * spread) + (int)(local * 22.0);
            lightness = 48 + (int)(low * 6.0 + local * 6.0 + beat_pulse * 8.0);
            if (lightness > 64)
                lightness = 64;
        }
        hue %= 256;
        if (hue < 0)
            hue += 256;
        uint8_t red, green, blue;
        if (mode == 2 && alpha <= 0.0) {
            if (palette == 0) {
                red = green = blue = 0u;
            } else {
                catalog_palette_hsl_to_rgb(0.0, 0.0, 36.0, palette,
                                           custom_rgb, &red, &green, &blue);
            }
        } else {
            palette_hsl_to_rgb((double)hue, saturation, (double)lightness, palette,
                               custom_rgb, &red, &green, &blue);
        }
        const size_t offset = (size_t)index * 4u;
        pixels[offset] = red;
        pixels[offset + 1u] = green;
        pixels[offset + 2u] = blue;
        pixels[offset + 3u] = output_alpha;
    }
    Py_RETURN_NONE;
}

PyObject *render_theater_chase_into(PyObject *module, PyObject *args) {
    (void)module;
    PyArrayObject *destination;
    int stride, audio_mode, palette;
    double phase, hue, drive, brightness;
    unsigned int custom_rgb;
    if (!PyArg_ParseTuple(args, "O!iddddiiI", &PyArray_Type, &destination,
                          &stride, &phase, &hue, &drive, &brightness,
                          &audio_mode, &palette, &custom_rgb))
        return NULL;
    if (validate_rgba(destination, "destination", 1) < 0)
        return NULL;
    if (stride < 1 || !isfinite(phase) || !isfinite(hue) || !isfinite(drive) ||
        !isfinite(brightness) || drive < 0.0 || drive > 1.0 ||
        brightness < 0.0 || brightness > 1.0 || (audio_mode != 0 && audio_mode != 1) ||
        palette < 0 || palette > 3 || custom_rgb > 0xFFFFFFu) {
        PyErr_SetString(PyExc_ValueError, "invalid TheaterChase render parameters");
        return NULL;
    }
    const npy_intp count = PyArray_DIM(destination, 0);
    uint8_t *pixels = (uint8_t *)PyArray_DATA(destination);
    for (npy_intp index = 0; index < count; index++) {
        const int slot = (int)(index % stride);
        double alpha = 0.0;
        int saturation = 100;
        int lightness = 55;
        if (audio_mode) {
            const double direct = fabs((double)slot - phase);
            const double distance = fmin(direct, (double)stride - direct);
            const double glow = fmax(1.0 - distance / 1.35, 0.0);
            alpha = glow * (0.72 + drive * 0.22);
            saturation = 96;
            lightness = 52;
            if (alpha <= 0.01)
                alpha = 0.0;
        } else {
            int center = (int)phase % stride;
            if (center < 0)
                center += stride;
            const int before = (center + stride - 1) % stride;
            const int after = (center + 1) % stride;
            if (slot == center) {
                alpha = 1.0;
            } else if (slot == before || slot == after) {
                alpha = 0.25;
                saturation = 80;
                lightness = 42;
            }
        }
        const size_t offset = (size_t)index * 4u;
        uint8_t red, green, blue;
        if (palette == 3) {
            red = (uint8_t)((custom_rgb >> 16u) & 0xFFu);
            green = (uint8_t)((custom_rgb >> 8u) & 0xFFu);
            blue = (uint8_t)(custom_rgb & 0xFFu);
        } else {
            if (alpha <= 0.0) {
                red = green = blue = 0u;
            } else {
                hsl_to_rgb(hue, (double)saturation, (double)lightness,
                           &red, &green, &blue);
            }
            if (palette == 1 || palette == 2)
                map_palette(&red, &green, &blue, palette);
        }
        pixels[offset] = red;
        pixels[offset + 1u] = green;
        pixels[offset + 2u] = blue;
        pixels[offset + 3u] = (uint8_t)(alpha * brightness * 255.0);
    }
    Py_RETURN_NONE;
}

PyObject *render_laser_sweep_into(PyObject *module, PyObject *args) {
    (void)module;
    PyArrayObject *destination;
    double position, width, hue, alpha, brightness;
    int palette;
    unsigned int custom_rgb;
    if (!PyArg_ParseTuple(args, "O!dddddiI", &PyArray_Type, &destination,
                          &position, &width, &hue, &alpha, &brightness,
                          &palette, &custom_rgb))
        return NULL;
    if (validate_rgba(destination, "destination", 1) < 0)
        return NULL;
    if (!isfinite(position) || !isfinite(width) || width <= 0.0 ||
        !isfinite(hue) || !isfinite(alpha) || alpha < 0.0 || alpha > 1.0 ||
        !isfinite(brightness) || brightness < 0.0 || brightness > 1.0 ||
        palette < 0 || palette > 3 || custom_rgb > 0xFFFFFFu) {
        PyErr_SetString(PyExc_ValueError, "invalid LaserSweep render parameters");
        return NULL;
    }
    const npy_intp count = PyArray_DIM(destination, 0);
    uint8_t *pixels = (uint8_t *)PyArray_DATA(destination);
    for (npy_intp index = 0; index < count; index++) {
        const double distance = fabs((double)index - position);
        double pixel_alpha = 0.0;
        if (distance < width)
            pixel_alpha = (1.0 - distance / width) * alpha;
        uint8_t red = 0u, green = 0u, blue = 0u;
        if (palette == 3) {
            red = (uint8_t)((custom_rgb >> 16u) & 0xFFu);
            green = (uint8_t)((custom_rgb >> 8u) & 0xFFu);
            blue = (uint8_t)(custom_rgb & 0xFFu);
        } else if (pixel_alpha > 0.0) {
            hsl_to_rgb(hue, 100.0, 60.0, &red, &green, &blue);
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

PyObject *render_sinelon_into(PyObject *module, PyObject *args) {
    (void)module;
    PyArrayObject *destination;
    double center, width, brightness;
    int hue, palette;
    unsigned int custom_rgb;
    if (!PyArg_ParseTuple(args, "O!ddidiI", &PyArray_Type, &destination,
                          &center, &width, &hue, &brightness,
                          &palette, &custom_rgb))
        return NULL;
    if (validate_rgba(destination, "destination", 1) < 0)
        return NULL;
    if (!isfinite(center) || !isfinite(width) || width <= 0.0 ||
        hue < 0 || hue > 255 || !isfinite(brightness) || brightness < 0.0 ||
        brightness > 1.0 || palette < 0 || palette > 3 || custom_rgb > 0xFFFFFFu) {
        PyErr_SetString(PyExc_ValueError, "invalid Sinelon render parameters");
        return NULL;
    }
    uint8_t custom_red = (uint8_t)((custom_rgb >> 16u) & 0xFFu);
    uint8_t custom_green = (uint8_t)((custom_rgb >> 8u) & 0xFFu);
    uint8_t custom_blue = (uint8_t)(custom_rgb & 0xFFu);
    uint8_t *pixels = (uint8_t *)PyArray_DATA(destination);
    const npy_intp count = PyArray_DIM(destination, 0);
    for (npy_intp index = 0; index < count; index++) {
        const double distance = fabs((double)index - center);
        const double alpha = distance < width ? 1.0 - distance / width : 0.0;
        uint8_t red = alpha > 0.0 ? (uint8_t)hue : 0u;
        uint8_t green = alpha > 0.0 ? 255u : 0u;
        uint8_t blue = alpha > 0.0 ? 255u : 0u;
        if (palette == 3) {
            red = custom_red;
            green = custom_green;
            blue = custom_blue;
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

PyObject *render_peak_mirror_into(PyObject *module, PyObject *args) {
    (void)module;
    PyArrayObject *destination;
    PyObject *levels_object;
    double center_flash, width, brightness;
    int hue_shift, palette;
    unsigned int custom_rgb;
    if (!PyArg_ParseTuple(args, "O!OdiddiI", &PyArray_Type, &destination,
                          &levels_object, &center_flash, &hue_shift, &width,
                          &brightness, &palette, &custom_rgb))
        return NULL;
    if (validate_rgba(destination, "destination", 1) < 0)
        return NULL;
    if (!isfinite(center_flash) || center_flash < 0.0 || center_flash > 1.0 ||
        !isfinite(width) || width <= 0.0 || !isfinite(brightness) ||
        brightness < 0.0 || brightness > 1.0 || hue_shift < 0 || hue_shift >= 360 ||
        palette < 0 || palette > 3 || custom_rgb > 0xFFFFFFu) {
        PyErr_SetString(PyExc_ValueError, "invalid PeakMirror render parameters");
        return NULL;
    }
    PyObject *levels = PySequence_Fast(levels_object, "levels must be a sequence");
    if (levels == NULL)
        return NULL;
    if (PySequence_Fast_GET_SIZE(levels) != 8) {
        Py_DECREF(levels);
        PyErr_SetString(PyExc_ValueError, "levels must contain exactly eight values");
        return NULL;
    }
    double values[8];
    for (int band = 0; band < 8; band++) {
        values[band] = PyFloat_AsDouble(PySequence_Fast_GET_ITEM(levels, band));
        if (PyErr_Occurred() || !isfinite(values[band])) {
            Py_DECREF(levels);
            if (!PyErr_Occurred())
                PyErr_SetString(PyExc_ValueError, "levels must be finite values");
            return NULL;
        }
    }
    Py_DECREF(levels);
    const npy_intp count = PyArray_DIM(destination, 0);
    const npy_intp mirror_span = count > 1 ? (count - 1) / 2 > 1 ? (count - 1) / 2 : 1 : 1;
    const double center = (double)(count - 1) / 2.0;
    uint8_t *pixels = (uint8_t *)PyArray_DATA(destination);
    for (npy_intp index = 0; index < count; index++) {
        const npy_intp mirrored = index < count - 1 - index ? index : count - 1 - index;
        const double position = count == 1 ? 1.0 : fmin((double)mirrored / (double)mirror_span, 1.0);
        const double band_position = position * 7.0;
        const int lower = (int)band_position;
        const int upper = lower + 1 < 8 ? lower + 1 : 7;
        const double blend = band_position - (double)lower;
        double level = values[lower] * (1.0 - blend) + values[upper] * blend;
        const double center_glow = fmax(0.0, 1.0 - fabs((double)index - center) / width);
        const double beat_glow = center_flash * center_glow;
        level = fmax(level, beat_glow);
        const size_t offset = (size_t)index * 4u;
        double alpha = 0.0;
        uint8_t red = 0u, green = 0u, blue = 0u;
        if (level > 0.015) {
            int hue = (hue_shift + (int)(band_position * 28.0)) % 360;
            const double lightness = fmin(48.0 + level * 12.0 + beat_glow * 6.0, 66.0);
            alpha = fmin(0.08 + level * 0.9, 1.0);
            if (palette == 3) {
                red = (uint8_t)((custom_rgb >> 16u) & 0xFFu);
                green = (uint8_t)((custom_rgb >> 8u) & 0xFFu);
                blue = (uint8_t)(custom_rgb & 0xFFu);
            } else {
                hsl_to_rgb((double)hue, 100.0, lightness, &red, &green, &blue);
                if (palette == 1 || palette == 2)
                    map_palette(&red, &green, &blue, palette);
            }
        } else if (palette == 3) {
            red = (uint8_t)((custom_rgb >> 16u) & 0xFFu);
            green = (uint8_t)((custom_rgb >> 8u) & 0xFFu);
            blue = (uint8_t)(custom_rgb & 0xFFu);
        } else if (palette == 1 || palette == 2) {
            map_palette(&red, &green, &blue, palette);
        }
        pixels[offset] = red;
        pixels[offset + 1u] = green;
        pixels[offset + 2u] = blue;
        pixels[offset + 3u] = (uint8_t)(alpha * brightness * 255.0);
    }
    Py_RETURN_NONE;
}

PyObject *render_bouncing_ball_into(PyObject *module, PyObject *args) {
    (void)module;
    PyArrayObject *destination;
    double position, radius, glow_width, glow, brightness;
    int hue, audio_mode, palette;
    unsigned int custom_rgb;
    if (!PyArg_ParseTuple(args, "O!ddddidiiI", &PyArray_Type, &destination,
                          &position, &radius, &glow_width, &glow, &hue,
                          &brightness, &audio_mode, &palette, &custom_rgb))
        return NULL;
    if (validate_rgba(destination, "destination", 1) < 0)
        return NULL;
    if (!isfinite(position) || !isfinite(radius) || radius <= 0.0 ||
        !isfinite(glow_width) || glow_width <= 0.0 || !isfinite(glow) ||
        glow < 0.0 || glow > 1.0 || hue < 0 || hue > 359 ||
        !isfinite(brightness) || brightness < 0.0 || brightness > 1.0 ||
        (audio_mode != 0 && audio_mode != 1) || palette < 0 || palette > 3 ||
        custom_rgb > 0xFFFFFFu) {
        PyErr_SetString(PyExc_ValueError, "invalid BouncingBall render parameters");
        return NULL;
    }
    uint8_t *pixels = (uint8_t *)PyArray_DATA(destination);
    const npy_intp count = PyArray_DIM(destination, 0);
    for (npy_intp index = 0; index < count; index++) {
        const double distance = fabs((double)index - position);
        double alpha = 0.0;
        int saturation = 100;
        int lightness = 56;
        if (!audio_mode) {
            if (distance < radius)
                alpha = 1.0 - distance / radius;
        } else if (distance < radius) {
            alpha = fmin(1.0 - distance / radius + glow * 0.3, 1.0);
        } else if (distance < radius + glow_width) {
            alpha = glow * (1.0 - (distance - radius) / glow_width);
            saturation = 90;
            lightness = 42;
            if (alpha <= 0.0)
                alpha = 0.0;
        }
        uint8_t red = 0u, green = 0u, blue = 0u;
        if (palette == 3) {
            red = (uint8_t)((custom_rgb >> 16u) & 0xFFu);
            green = (uint8_t)((custom_rgb >> 8u) & 0xFFu);
            blue = (uint8_t)(custom_rgb & 0xFFu);
        } else if (alpha > 0.0) {
            hsl_to_rgb((double)hue, (double)saturation, (double)lightness,
                       &red, &green, &blue);
            if (palette == 1 || palette == 2)
                map_palette(&red, &green, &blue, palette);
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

PyObject *render_wave_into(PyObject *module, PyObject *args) {
    (void)module;
    PyArrayObject *destination;
    PyObject *bands_object;
    Py_ssize_t frame;
    int audio_mode, palette;
    double frequency, speed, low, mid, high, accent, brightness;
    unsigned int custom_rgb;
    if (!PyArg_ParseTuple(args, "O!niOdddddddiI", &PyArray_Type, &destination,
                          &frame, &audio_mode, &bands_object, &frequency, &speed,
                          &low, &mid, &high, &accent, &brightness,
                          &palette, &custom_rgb))
        return NULL;
    if (validate_rgba(destination, "destination", 1) < 0)
        return NULL;
    if ((audio_mode != 0 && audio_mode != 1) || !isfinite(frequency) ||
        !isfinite(speed) || !isfinite(low) || !isfinite(mid) || !isfinite(high) ||
        !isfinite(accent) || !isfinite(brightness) || brightness < 0.0 ||
        brightness > 1.0 || palette < 0 || palette > 3 || custom_rgb > 0xFFFFFFu) {
        PyErr_SetString(PyExc_ValueError, "invalid Wave render parameters");
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
            if (PyErr_Occurred() || !isfinite(bands[band])) {
                Py_DECREF(sequence);
                if (!PyErr_Occurred())
                    PyErr_SetString(PyExc_ValueError, "bands must be finite values");
                return NULL;
            }
        }
        Py_DECREF(sequence);
    }
    uint8_t *pixels = (uint8_t *)PyArray_DATA(destination);
    const npy_intp count = PyArray_DIM(destination, 0);
    for (npy_intp index = 0; index < count; index++) {
        const double position = count <= 1 ? 0.0 : (double)index / (double)(count - 1);
        double local = 0.0;
        if (audio_mode) {
            if (count <= 1) {
                local = bands[0];
            } else {
                const double band_position = (double)index * 8.0 / (double)(count - 1);
                const int center = (int)band_position;
                const int start = center > 0 ? center - 1 : 0;
                const int end = center + 2 < 8 ? center + 2 : 8;
                double total = 0.0;
                for (int band = start; band < end; band++) {
                    total += bands[band];
                }
                local = total / (double)(end - start);
            }
        }
        uint8_t red, green, blue;
        double alpha;
        if (!audio_mode) {
            const double phase = position * 5.5 * frequency + (double)frame * 0.28;
            const double crest = sin(phase) * 0.5 + 0.5;
            const int hue = ((int)(frame + index) * 2) % 256;
            red = 0u;
            green = (uint8_t)hue;
            blue = 255u;
            alpha = 0.22 + crest * 0.78;
        } else {
            const double amplitude = 0.24 + local * 0.76;
            const double phase = position * 5.5 * frequency + (double)frame * speed + low * 2.0;
            const double crest = (sin(phase) * 0.5 + 0.5) * amplitude;
            const double hue_rate = 0.08 + high * 3.0 + accent * 6.0;
            int hue = (int)((double)(frame + index) * hue_rate) % 256;
            if (hue < 0)
                hue += 256;
            hue = (160 + hue / 6) % 256;
            const int lightness = (int)fmin(56.0 + mid * 8.0 + crest * 6.0, 68.0);
            hsl_to_rgb((double)hue, 100.0, (double)lightness, &red, &green, &blue);
            alpha = 0.22 + crest * 0.78;
        }
        if (palette == 3) {
            red = (uint8_t)((custom_rgb >> 16u) & 0xFFu);
            green = (uint8_t)((custom_rgb >> 8u) & 0xFFu);
            blue = (uint8_t)(custom_rgb & 0xFFu);
        } else {
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

PyObject *render_juggle_into(PyObject *module, PyObject *args) {
    (void)module;
    PyArrayObject *destination;
    PyObject *positions_object;
    Py_ssize_t frame;
    double radius, hue_rate, brightness;
    int audio_mode, palette;
    unsigned int custom_rgb;
    if (!PyArg_ParseTuple(args, "O!OdnddiiI", &PyArray_Type, &destination,
                          &positions_object, &radius, &frame, &hue_rate,
                          &brightness, &audio_mode, &palette, &custom_rgb))
        return NULL;
    if (validate_rgba(destination, "destination", 1) < 0)
        return NULL;
    if (!isfinite(radius) || radius <= 0.0 || !isfinite(hue_rate) || hue_rate < 0.0 ||
        !isfinite(brightness) || brightness < 0.0 || brightness > 1.0 ||
        (audio_mode != 0 && audio_mode != 1) || palette < 0 || palette > 3 ||
        custom_rgb > 0xFFFFFFu) {
        PyErr_SetString(PyExc_ValueError, "invalid Juggle render parameters");
        return NULL;
    }
    PyObject *positions_sequence = PySequence_Fast(positions_object, "positions must be a sequence");
    if (positions_sequence == NULL)
        return NULL;
    const Py_ssize_t position_count = PySequence_Fast_GET_SIZE(positions_sequence);
    if (position_count < 1 || position_count > 32) {
        Py_DECREF(positions_sequence);
        PyErr_SetString(PyExc_ValueError, "Juggle requires between one and 32 positions");
        return NULL;
    }
    double positions[32];
    for (Py_ssize_t ball = 0; ball < position_count; ball++) {
        positions[ball] = PyFloat_AsDouble(PySequence_Fast_GET_ITEM(positions_sequence, ball));
        if (PyErr_Occurred() || !isfinite(positions[ball])) {
            Py_DECREF(positions_sequence);
            if (!PyErr_Occurred())
                PyErr_SetString(PyExc_ValueError, "positions must be finite values");
            return NULL;
        }
    }
    Py_DECREF(positions_sequence);
    uint8_t *pixels = (uint8_t *)PyArray_DATA(destination);
    const npy_intp count = PyArray_DIM(destination, 0);
    for (npy_intp index = 0; index < count; index++) {
        double composite = 0.0;
        for (Py_ssize_t ball = 0; ball < position_count; ball++) {
            const double distance = fabs((double)index - positions[ball]);
            if (distance < radius)
                composite += (1.0 - distance / radius) * 0.6;
        }
        const double alpha = fmin(composite, 1.0);
        uint8_t red = 0u, green = 0u, blue = 0u;
        if (palette == 3) {
            red = (uint8_t)((custom_rgb >> 16u) & 0xFFu);
            green = (uint8_t)((custom_rgb >> 8u) & 0xFFu);
            blue = (uint8_t)(custom_rgb & 0xFFu);
        } else if (audio_mode && alpha > 0.0) {
            int hue = ((int)((double)(frame + index) * hue_rate) + (int)(index * 11 % 128)) % 256;
            red = (uint8_t)hue;
            green = 255u;
            blue = 200u;
            if (palette == 1 || palette == 2)
                map_palette(&red, &green, &blue, palette);
        } else if (!audio_mode && alpha > 0.0) {
            red = (uint8_t)((index * 32 + 50) % 256);
            green = 255u;
            blue = 200u;
            if (palette == 1 || palette == 2)
                map_palette(&red, &green, &blue, palette);
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

static PyMethodDef methods[] = {
    {"render_rainbow_pattern_into", render_rainbow_pattern_into, METH_VARARGS,
     "Render Rainbow or RainbowCycle static/audio patterns into an RGBA frame."},
    {"render_theater_chase_into", render_theater_chase_into, METH_VARARGS,
     "Render TheaterChase static/audio pixels into an RGBA frame."},
    {"render_laser_sweep_into", render_laser_sweep_into, METH_VARARGS,
     "Render a moving LaserSweep beam into an RGBA frame."},
    {"render_sinelon_into", render_sinelon_into, METH_VARARGS,
     "Render a Sinelon moving pulse into an RGBA frame."},
    {"render_peak_mirror_into", render_peak_mirror_into, METH_VARARGS,
     "Render PeakMirror level interpolation and center flash into an RGBA frame."},
    {"render_bouncing_ball_into", render_bouncing_ball_into, METH_VARARGS,
     "Render a BouncingBall core and audio glow into an RGBA frame."},
    {"render_wave_into", render_wave_into, METH_VARARGS,
     "Render Wave static or audio-reactive frames into an RGBA buffer."},
    {"render_juggle_into", render_juggle_into, METH_VARARGS,
     "Render overlapping Juggle particle trails into an RGBA frame."},
    {"render_tunnel_rings_into", render_tunnel_rings_into, METH_VARARGS,
     "Render up to two alpha-weighted BeatTunnel rings into an RGBA frame."},
    {NULL, NULL, 0, NULL}
};

PyMethodDef *native_patterns_methods(void) { return methods; }
