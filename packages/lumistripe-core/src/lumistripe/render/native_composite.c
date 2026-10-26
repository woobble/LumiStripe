#include "native_common.h"
PyObject *correct_rgba_into(PyObject *module, PyObject *args) {
    (void)module;
    PyArrayObject *source;
    PyArrayObject *destination;
    PyArrayObject *correction;
    if (!PyArg_ParseTuple(args, "O!O!O!", &PyArray_Type, &source,
                          &PyArray_Type, &destination, &PyArray_Type, &correction)) {
        return NULL;
    }
    if (validate_rgba(source, "source", 0) < 0 ||
        validate_rgba(destination, "destination", 1) < 0) {
        return NULL;
    }
    if (PyArray_DIM(source, 0) != PyArray_DIM(destination, 0)) {
        PyErr_SetString(PyExc_ValueError, "source and destination must have the same shape");
        return NULL;
    }
    if (PyArray_TYPE(correction) != NPY_UINT16 || PyArray_NDIM(correction) != 1 ||
        PyArray_DIM(correction, 0) != 3 || !PyArray_ISCARRAY_RO(correction)) {
        PyErr_SetString(PyExc_ValueError, "correction must be a contiguous uint16 array of shape (3,)");
        return NULL;
    }
    if (reject_partial_overlap(source, destination, "source", "destination") < 0 ||
        reject_partial_overlap(source, correction, "source", "correction") < 0 ||
        reject_partial_overlap(destination, correction, "destination", "correction") < 0)
        return NULL;

    const uint8_t *src = (const uint8_t *)PyArray_DATA(source);
    uint8_t *dst = (uint8_t *)PyArray_DATA(destination);
    const uint16_t *rgb = (const uint16_t *)PyArray_DATA(correction);
    if (rgb[0] > 255u || rgb[1] > 255u || rgb[2] > 255u) {
        PyErr_SetString(PyExc_ValueError, "correction channels must be between 0 and 255");
        return NULL;
    }
    const npy_intp count = PyArray_DIM(source, 0);
    for (npy_intp index = 0; index < count; index++) {
        const size_t offset = (size_t)index * 4u;
        dst[offset] = (uint8_t)((uint32_t)src[offset] * rgb[0] / 255u);
        dst[offset + 1u] = (uint8_t)((uint32_t)src[offset + 1u] * rgb[1] / 255u);
        dst[offset + 2u] = (uint8_t)((uint32_t)src[offset + 2u] * rgb[2] / 255u);
        dst[offset + 3u] = src[offset + 3u];
    }
    Py_RETURN_NONE;
}

PyObject *apply_alpha_scale_into(PyObject *module, PyObject *args) {
    (void)module;
    PyArrayObject *source;
    PyArrayObject *corrected;
    PyArrayObject *destination;
    double scale;
    if (!PyArg_ParseTuple(args, "O!O!O!d", &PyArray_Type, &source,
                          &PyArray_Type, &corrected, &PyArray_Type, &destination,
                          &scale)) {
        return NULL;
    }
    if (validate_rgba(source, "source", 0) < 0 ||
        validate_rgba(corrected, "corrected", 1) < 0 ||
        validate_rgba(destination, "destination", 1) < 0) {
        return NULL;
    }
    if (PyArray_DIM(source, 0) != PyArray_DIM(corrected, 0) ||
        PyArray_DIM(source, 0) != PyArray_DIM(destination, 0)) {
        PyErr_SetString(PyExc_ValueError, "source, corrected, and destination must have the same shape");
        return NULL;
    }
    if (reject_partial_overlap(source, corrected, "source", "corrected") < 0 ||
        reject_partial_overlap(source, destination, "source", "destination") < 0 ||
        reject_partial_overlap(corrected, destination, "corrected", "destination") < 0)
        return NULL;
    if (!(scale >= 0.0 && scale <= 1.0)) {
        PyErr_SetString(PyExc_ValueError, "alpha scale must be between zero and one");
        return NULL;
    }

    const uint8_t *src = (const uint8_t *)PyArray_DATA(source);
    uint8_t *preview = (uint8_t *)PyArray_DATA(corrected);
    uint8_t *dst = (uint8_t *)PyArray_DATA(destination);
    const npy_intp count = PyArray_DIM(source, 0);
    for (npy_intp index = 0; index < count; index++) {
        const size_t offset = (size_t)index * 4u;
        const uint8_t alpha = (uint8_t)((double)src[offset + 3u] * scale);
        dst[offset] = src[offset];
        dst[offset + 1u] = src[offset + 1u];
        dst[offset + 2u] = src[offset + 2u];
        dst[offset + 3u] = alpha;
        preview[offset + 3u] = alpha;
    }
    Py_RETURN_NONE;
}

PyObject *render_solid_rgba_into(PyObject *module, PyObject *args) {
    (void)module;
    PyArrayObject *destination;
    unsigned int red, green, blue, alpha;
    if (!PyArg_ParseTuple(args, "O!IIII", &PyArray_Type, &destination,
                          &red, &green, &blue, &alpha))
        return NULL;
    if (validate_rgba(destination, "destination", 1) < 0)
        return NULL;
    if (red > 255u || green > 255u || blue > 255u || alpha > 255u) {
        PyErr_SetString(PyExc_ValueError, "RGBA channels must be between 0 and 255");
        return NULL;
    }
    uint8_t *pixels = (uint8_t *)PyArray_DATA(destination);
    const npy_intp count = PyArray_DIM(destination, 0);
    for (npy_intp index = 0; index < count; index++) {
        const size_t offset = (size_t)index * 4u;
        pixels[offset] = (uint8_t)red;
        pixels[offset + 1u] = (uint8_t)green;
        pixels[offset + 2u] = (uint8_t)blue;
        pixels[offset + 3u] = (uint8_t)alpha;
    }
    Py_RETURN_NONE;
}

PyObject *render_hsl_solid_into(PyObject *module, PyObject *args) {
    (void)module;
    PyArrayObject *destination;
    double hue, saturation, lightness, alpha, brightness;
    if (!PyArg_ParseTuple(args, "O!ddddd", &PyArray_Type, &destination,
                          &hue, &saturation, &lightness, &alpha, &brightness))
        return NULL;
    if (validate_rgba(destination, "destination", 1) < 0)
        return NULL;
    if (!isfinite(hue) || !isfinite(saturation) || !isfinite(lightness) ||
        !isfinite(alpha) || !isfinite(brightness) || alpha < 0.0 || alpha > 1.0 ||
        brightness < 0.0 || brightness > 1.0) {
        PyErr_SetString(PyExc_ValueError, "HSL render values must be finite and in range");
        return NULL;
    }
    uint8_t red, green, blue;
    hsl_to_rgb(hue, saturation, lightness, &red, &green, &blue);
    const uint8_t output_alpha = (uint8_t)(alpha * brightness * 255.0);
    uint8_t *pixels = (uint8_t *)PyArray_DATA(destination);
    const npy_intp count = PyArray_DIM(destination, 0);
    for (npy_intp index = 0; index < count; index++) {
        const size_t offset = (size_t)index * 4u;
        pixels[offset] = red;
        pixels[offset + 1u] = green;
        pixels[offset + 2u] = blue;
        pixels[offset + 3u] = output_alpha;
    }
    Py_RETURN_NONE;
}

PyObject *render_halves_rgba_into(PyObject *module, PyObject *args) {
    (void)module;
    PyArrayObject *destination;
    unsigned int left[4], right[4];
    if (!PyArg_ParseTuple(args, "O!IIIIIIII", &PyArray_Type, &destination,
                          &left[0], &left[1], &left[2], &left[3],
                          &right[0], &right[1], &right[2], &right[3]))
        return NULL;
    if (validate_rgba(destination, "destination", 1) < 0)
        return NULL;
    for (size_t i = 0; i < 4u; i++) {
        if (left[i] > 255u || right[i] > 255u) {
            PyErr_SetString(PyExc_ValueError, "RGBA channels must be between 0 and 255");
            return NULL;
        }
    }
    uint8_t *pixels = (uint8_t *)PyArray_DATA(destination);
    const npy_intp count = PyArray_DIM(destination, 0);
    const npy_intp half = count / 2;
    for (npy_intp index = 0; index < count; index++) {
        const unsigned int *color = index < half ? left : right;
        const size_t offset = (size_t)index * 4u;
        for (size_t channel = 0; channel < 4u; channel++)
            pixels[offset + channel] = (uint8_t)color[channel];
    }
    Py_RETURN_NONE;
}

PyObject *render_red_sweep_into(PyObject *module, PyObject *args) {
    (void)module;
    PyArrayObject *destination;
    Py_ssize_t start, width;
    int on;
    unsigned int alpha;
    if (!PyArg_ParseTuple(args, "O!nnpI", &PyArray_Type, &destination,
                          &start, &width, &on, &alpha))
        return NULL;
    if (validate_rgba(destination, "destination", 1) < 0)
        return NULL;
    if (start < 0 || width < 0 || alpha > 255u) {
        PyErr_SetString(PyExc_ValueError, "start and width must be non-negative");
        return NULL;
    }
    uint8_t *pixels = (uint8_t *)PyArray_DATA(destination);
    const npy_intp count = PyArray_DIM(destination, 0);
    for (npy_intp index = 0; index < count; index++) {
        const int illuminated = on && index >= start && index - start < width;
        const size_t offset = (size_t)index * 4u;
        pixels[offset] = illuminated ? 255u : 0u;
        pixels[offset + 1u] = 0u;
        pixels[offset + 2u] = 0u;
        pixels[offset + 3u] = illuminated ? (uint8_t)alpha : 255u;
    }
    Py_RETURN_NONE;
}

PyObject *render_red_chase_into(PyObject *module, PyObject *args) {
    (void)module;
    PyArrayObject *destination;
    Py_ssize_t period, block;
    double phase;
    unsigned int alpha;
    if (!PyArg_ParseTuple(args, "O!nndI", &PyArray_Type, &destination,
                          &period, &block, &phase, &alpha))
        return NULL;
    if (validate_rgba(destination, "destination", 1) < 0)
        return NULL;
    if (period < 1 || block < 0 || block > period || !isfinite(phase) || alpha > 255u) {
        PyErr_SetString(PyExc_ValueError, "invalid red chase geometry");
        return NULL;
    }
    uint8_t *pixels = (uint8_t *)PyArray_DATA(destination);
    const npy_intp count = PyArray_DIM(destination, 0);
    const npy_intp midpoint = (count + 1) / 2;
    for (npy_intp index = 0; index < count; index++) {
        const npy_intp mirrored = index < midpoint ? index : count - 1 - index;
        double position = fmod((double)mirrored + phase, (double)period);
        if (position < 0.0)
            position += (double)period;
        const int illuminated = position < (double)block;
        const size_t offset = (size_t)index * 4u;
        pixels[offset] = illuminated ? 255u : 0u;
        pixels[offset + 1u] = 0u;
        pixels[offset + 2u] = 0u;
        pixels[offset + 3u] = illuminated ? (uint8_t)alpha : 255u;
    }
    Py_RETURN_NONE;
}

PyObject *render_drop_explosion_into(PyObject *module, PyObject *args) {
    (void)module;
    PyArrayObject *destination;
    Py_ssize_t frame;
    double flash_alpha, tension, preview, low, high, brightness;
    int audio_mode;
    if (!PyArg_ParseTuple(args, "O!ndddpddd", &PyArray_Type, &destination,
                          &frame, &flash_alpha, &tension, &preview,
                          &audio_mode, &low, &high, &brightness))
        return NULL;
    if (validate_rgba(destination, "destination", 1) < 0)
        return NULL;
    if (!isfinite(flash_alpha) || !isfinite(tension) || !isfinite(preview) ||
        !isfinite(low) || !isfinite(high) || !isfinite(brightness) ||
        flash_alpha < 0.0 || tension < 0.0 || brightness < 0.0 || brightness > 1.0) {
        PyErr_SetString(PyExc_ValueError, "drop explosion render values are out of range");
        return NULL;
    }
    uint8_t *pixels = (uint8_t *)PyArray_DATA(destination);
    const npy_intp count = PyArray_DIM(destination, 0);
    for (npy_intp index = 0; index < count; index++) {
        unsigned int red = 0u, green = 0u, blue = 0u;
        double alpha = 0.0;
        if (!audio_mode) {
            const double flicker = (double)((frame + index * 7) % 6) / 6.0;
            if (flash_alpha > 0.5) {
                red = green = blue = 255u;
                alpha = flash_alpha;
            } else if (tension > 0.7 && preview > 0.3) {
                red = 80u; green = 40u; blue = 20u;
                alpha = preview * 0.5 + flicker * 0.2;
            } else if (tension > 0.3) {
                const double pulse = (double)((frame * 3 + index) % 30) / 30.0;
                red = 30u; green = 10u; blue = 5u;
                alpha = tension * 0.15 + pulse * 0.08;
            }
        } else {
            const double flicker = (double)((frame + index * 7) % 5) / 5.0 * high * 0.3;
            if (flash_alpha > 0.4) {
                const int warmth = (int)(low * 80.0 + 175.0);
                red = 255u; green = (unsigned int)warmth; blue = (unsigned int)(warmth / 2);
                alpha = flash_alpha;
            } else if (tension > 0.65 && preview > 0.2) {
                const int warmth = (int)(40.0 + low * 60.0);
                red = (unsigned int)warmth;
                green = (unsigned int)(warmth / 3);
                blue = (unsigned int)(warmth / 6);
                alpha = preview * 0.6 + flicker;
                if (alpha > 1.0) alpha = 1.0;
            } else if (tension > 0.3) {
                const double pulse = (double)((frame * 2 + index) % 24) / 24.0;
                red = 20u; green = 8u; blue = 3u;
                alpha = tension * 0.12 + pulse * 0.06;
            }
        }
        if (alpha < 0.0) alpha = 0.0;
        if (alpha > 1.0) alpha = 1.0;
        const size_t offset = (size_t)index * 4u;
        pixels[offset] = (uint8_t)red;
        pixels[offset + 1u] = (uint8_t)green;
        pixels[offset + 2u] = (uint8_t)blue;
        pixels[offset + 3u] = (uint8_t)(alpha * brightness * 255.0);
    }
    Py_RETURN_NONE;
}

PyObject *render_fire_into(PyObject *module, PyObject *args) {
    (void)module;
    PyArrayObject *destination, *heat_array, *next_array;
    Py_ssize_t frame;
    double cooling, sparking, drive, bass, shimmer, accent, brightness;
    if (!PyArg_ParseTuple(args, "O!O!O!nddddddd", &PyArray_Type, &destination,
                          &PyArray_Type, &heat_array, &PyArray_Type, &next_array,
                          &frame, &cooling, &sparking, &drive, &bass, &shimmer,
                          &accent, &brightness))
        return NULL;
    if (validate_rgba(destination, "destination", 1) < 0)
        return NULL;
    const npy_intp count = PyArray_DIM(destination, 0);
    const npy_intp state_shape[1] = {count};
    if (validate_array(heat_array, NPY_FLOAT32, 1, state_shape, 0, 0.0, 1.0,
                       "heat") < 0 ||
        validate_array(next_array, NPY_FLOAT32, 1, state_shape, 1, -INFINITY,
                       INFINITY, "next heat") < 0 ||
        reject_any_overlap(destination, heat_array, "destination", "heat") < 0 ||
        reject_any_overlap(destination, next_array, "destination", "next heat") < 0 ||
        reject_any_overlap(heat_array, next_array, "heat", "next heat") < 0) {
        return NULL;
    }
    if (!isfinite(cooling) || !isfinite(sparking) || !isfinite(drive) ||
        !isfinite(bass) || !isfinite(shimmer) || !isfinite(accent) ||
        !isfinite(brightness) || brightness < 0.0 || brightness > 1.0) {
        PyErr_SetString(PyExc_ValueError, "fire render parameters must be finite and brightness in range");
        return NULL;
    }
    const float *heat = (const float *)PyArray_DATA(heat_array);
    float *next = (float *)PyArray_DATA(next_array);
    const double retention = 0.94 - cooling * 0.16 + drive * 0.04;
    const double spark_threshold = 0.96 - sparking * 0.58 - shimmer * 0.10 - accent * 0.06;
    const double spark_strength = 0.10 + sparking * 0.34 + drive * 0.18 + accent * 0.08;
    const double fuel = 0.015 + bass * 0.025;
    const uint64_t seed_base = (uint64_t)frame * UINT64_C(2654435761);
    for (npy_intp index = 0; index < count; index++) {
        const uint64_t seed = seed_base + (uint64_t)index * UINT64_C(314159);
        const double noise = (double)((seed >> 16u) & UINT64_C(0xFF)) / 255.0;
        const float current = heat[index];
        const float left = index > 0 ? heat[index - 1] : heat[index];
        const float right = index + 1 < count ? heat[index + 1] : heat[index];
        const float neighbors = left + right;
        volatile float spread = current * 0.68f + neighbors * 0.14f;
        volatile float retained = spread * (float)retention;
        const float spark = (float)(fmax(0.0, noise - spark_threshold) * spark_strength);
        volatile float updated = retained + spark;
        updated = updated + (float)fuel;
        next[index] = fminf(1.0f, updated);
    }

    const uint8_t output_alpha = (uint8_t)(brightness * 255.0);
    uint8_t *pixels = (uint8_t *)PyArray_DATA(destination);
    for (npy_intp index = 0; index < count; index++) {
        const double heat_value = (double)next[index];
        double intensity = (heat_value - 0.025) / 0.7;
        if (intensity < 0.0) intensity = 0.0;
        if (intensity > 1.0) intensity = 1.0;
        unsigned int red = 0u, green = 0u, blue = 0u;
        if (intensity > 0.0 && intensity < 0.35) {
            const double phase = intensity / 0.35;
            red = (unsigned int)(35.0 + phase * 140.0);
            green = (unsigned int)(phase * 15.0);
        } else if (intensity >= 0.35 && intensity < 0.75) {
            const double phase = (intensity - 0.35) / 0.4;
            red = (unsigned int)(175.0 + phase * 80.0);
            green = (unsigned int)(15.0 + phase * 100.0);
            blue = (unsigned int)(phase * 8.0);
        } else if (intensity >= 0.75) {
            const double phase = (intensity - 0.75) / 0.25;
            red = 255u;
            green = (unsigned int)(115.0 + phase * 100.0);
            blue = (unsigned int)(8.0 + phase * 18.0);
        }
        const size_t offset = (size_t)index * 4u;
        pixels[offset] = (uint8_t)red;
        pixels[offset + 1u] = (uint8_t)green;
        pixels[offset + 2u] = (uint8_t)blue;
        pixels[offset + 3u] = output_alpha;
    }
    Py_RETURN_NONE;
}

PyObject *render_color_wipe_into(PyObject *module, PyObject *args) {
    (void)module;
    PyArrayObject *destination;
    Py_ssize_t length;
    double head, tail, hue, saturation, local, boost, brightness;
    int direction;
    if (!PyArg_ParseTuple(args, "O!nddddiddd", &PyArray_Type, &destination,
                          &length, &head, &tail, &hue, &saturation, &direction,
                          &local, &boost, &brightness))
        return NULL;
    if (validate_rgba(destination, "destination", 1) < 0)
        return NULL;
    if (length < 1 || length != PyArray_DIM(destination, 0) || tail <= 0.0 ||
        (direction != -1 && direction != 1) || !isfinite(head) ||
        !isfinite(tail) || !isfinite(hue) || !isfinite(saturation) ||
        !isfinite(local) || !isfinite(boost) || !isfinite(brightness) ||
        brightness < 0.0 || brightness > 1.0) {
        PyErr_SetString(PyExc_ValueError, "color wipe render geometry is invalid");
        return NULL;
    }
    const uint8_t output_alpha = (uint8_t)(brightness * 255.0);
    uint8_t *pixels = (uint8_t *)PyArray_DATA(destination);
    for (npy_intp index = 0; index < length; index++) {
        double distance = direction > 0
            ? fmod(head - (double)index, (double)length)
            : fmod((double)index - head, (double)length);
        if (distance < 0.0)
            distance += (double)length;
        const double fade = fmax(0.0, 1.0 - distance / tail);
        int lightness = 44 + (int)(fade * 18.0 + local * 12.0 + boost * 10.0);
        if (lightness > 66)
            lightness = 66;
        uint8_t red, green, blue;
        hsl_to_rgb(hue, saturation, (double)lightness, &red, &green, &blue);
        const size_t offset = (size_t)index * 4u;
        pixels[offset] = red;
        pixels[offset + 1u] = green;
        pixels[offset + 2u] = blue;
        pixels[offset + 3u] = output_alpha;
    }
    Py_RETURN_NONE;
}

static PyArrayObject *optional_rgba_array(PyObject *value, const char *name,
                                          npy_intp expected_length) {
    if (value == Py_None)
        return NULL;
    if (!PyArray_Check(value)) {
        PyErr_Format(PyExc_TypeError, "%s must be a NumPy array or None", name);
        return NULL;
    }
    PyArrayObject *array = (PyArrayObject *)value;
    if (validate_rgba(array, name, 0) < 0)
        return NULL;
    if (PyArray_DIM(array, 0) != expected_length) {
        PyErr_Format(PyExc_ValueError, "%s must have the same length as base", name);
        return NULL;
    }
    return array;
}

static float visible_channel(const uint8_t *pixel, size_t channel) {
    const float normalized = (float)pixel[channel] / 255.0f;
    const float alpha = (float)pixel[3u] / 255.0f;
    return normalized * alpha;
}

static float screen_blend(float composed, float overlay) {
    const float base_remaining = 1.0f - composed;
    const float overlay_remaining = 1.0f - overlay;
    volatile float remaining = base_remaining * overlay_remaining;
    return 1.0f - remaining;
}

PyObject *composite_screen_rgba_into(PyObject *module, PyObject *args) {
    (void)module;
    PyArrayObject *base;
    PyArrayObject *destination;
    PyObject *overlay_1_object;
    PyObject *overlay_2_object;
    double base_intensity;
    double strength_1;
    double strength_2;
    if (!PyArg_ParseTuple(args, "O!O!dOdOd", &PyArray_Type, &base,
                          &PyArray_Type, &destination, &base_intensity,
                          &overlay_1_object, &strength_1, &overlay_2_object,
                          &strength_2)) {
        return NULL;
    }
    if (validate_rgba(base, "base", 0) < 0 ||
        validate_rgba(destination, "destination", 1) < 0) {
        return NULL;
    }
    const npy_intp count = PyArray_DIM(base, 0);
    if (PyArray_DIM(destination, 0) != count) {
        PyErr_SetString(PyExc_ValueError, "base and destination must have the same shape");
        return NULL;
    }
    if (!isfinite(base_intensity) || !isfinite(strength_1) ||
        !isfinite(strength_2) || strength_1 < 0.0 || strength_2 < 0.0) {
        PyErr_SetString(
            PyExc_ValueError,
            "intensity and strengths must be finite, with non-negative strengths"
        );
        return NULL;
    }
    PyArrayObject *overlay_1 = optional_rgba_array(
        overlay_1_object, "overlay_1", count
    );
    if (overlay_1_object != Py_None && overlay_1 == NULL)
        return NULL;
    PyArrayObject *overlay_2 = optional_rgba_array(
        overlay_2_object, "overlay_2", count
    );
    if (overlay_2_object != Py_None && overlay_2 == NULL)
        return NULL;
    if (reject_partial_overlap(base, destination, "base", "destination") < 0 ||
        (overlay_1 != NULL &&
         reject_partial_overlap(overlay_1, destination, "overlay_1", "destination") < 0) ||
        (overlay_2 != NULL &&
         reject_partial_overlap(overlay_2, destination, "overlay_2", "destination") < 0))
        return NULL;

    const uint8_t *base_pixels = (const uint8_t *)PyArray_DATA(base);
    const uint8_t *overlay_1_pixels = overlay_1 == NULL
        ? NULL
        : (const uint8_t *)PyArray_DATA(overlay_1);
    const uint8_t *overlay_2_pixels = overlay_2 == NULL
        ? NULL
        : (const uint8_t *)PyArray_DATA(overlay_2);
    uint8_t *output = (uint8_t *)PyArray_DATA(destination);
    const float base_scale = (float)base_intensity;
    const float first_scale = (float)strength_1;
    const float second_scale = (float)strength_2;

    for (npy_intp index = 0; index < count; index++) {
        const size_t offset = (size_t)index * 4u;
        const uint8_t *base_pixel = base_pixels + offset;
        const uint8_t *first_pixel = overlay_1_pixels == NULL
            ? NULL
            : overlay_1_pixels + offset;
        const uint8_t *second_pixel = overlay_2_pixels == NULL
            ? NULL
            : overlay_2_pixels + offset;
        for (size_t channel = 0; channel < 3u; channel++) {
            float composed = visible_channel(base_pixel, channel) * base_scale;
            if (first_pixel != NULL && first_scale > 0.0f) {
                const float visible = visible_channel(first_pixel, channel) * first_scale;
                composed = screen_blend(composed, visible);
            }
            if (second_pixel != NULL && second_scale > 0.0f) {
                const float visible = visible_channel(second_pixel, channel) * second_scale;
                composed = screen_blend(composed, visible);
            }
            const float scaled = composed * 255.0f;
            uint8_t value;
            if (!(scaled > 0.0f))
                value = 0u;
            else if (scaled >= 255.0f)
                value = 255u;
            else
                value = (uint8_t)scaled;
            output[offset + channel] = value;
        }
        output[offset + 3u] = 255u;
    }
    Py_RETURN_NONE;
}

static PyMethodDef methods[] = {
    {"correct_rgba_into", correct_rgba_into, METH_VARARGS,
     "Apply integer RGB correction into a contiguous RGBA destination."},
    {"apply_alpha_scale_into", apply_alpha_scale_into, METH_VARARGS,
     "Copy RGBA pixels and floor-scale alpha into reusable destinations."},
    {"render_solid_rgba_into", render_solid_rgba_into, METH_VARARGS,
     "Fill an RGBA destination with one precomputed pixel."},
    {"render_hsl_solid_into", render_hsl_solid_into, METH_VARARGS,
     "Convert one HSL color and fill an RGBA destination."},
    {"render_halves_rgba_into", render_halves_rgba_into, METH_VARARGS,
     "Fill the two halves of an RGBA destination with separate pixels."},
    {"render_red_sweep_into", render_red_sweep_into, METH_VARARGS,
     "Render a hard-edged red sweep into an RGBA frame."},
    {"render_red_chase_into", render_red_chase_into, METH_VARARGS,
     "Render mirrored red chase blocks into an RGBA frame."},
    {"render_drop_explosion_into", render_drop_explosion_into, METH_VARARGS,
     "Render DropExplosion static or audio-reactive pixels into an RGBA frame."},
    {"render_fire_into", render_fire_into, METH_VARARGS,
     "Advance the Fire heat buffer and render its color ramp into an RGBA frame."},
    {"render_color_wipe_into", render_color_wipe_into, METH_VARARGS,
     "Render a traveling ColorWipe gradient into an RGBA frame."},
    {"composite_screen_rgba_into", composite_screen_rgba_into, METH_VARARGS,
     "Composite up to two alpha-weighted RGBA overlays using SCREEN blending."},
    {NULL, NULL, 0, NULL}
};

PyMethodDef *native_composite_methods(void) { return methods; }
