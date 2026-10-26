#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <numpy/arrayobject.h>
#include <math.h>
#include <stdint.h>
#include <stdlib.h>

static int validate_rgba(PyArrayObject *array, const char *name, int writable) {
    if (PyArray_TYPE(array) != NPY_UINT8 || PyArray_NDIM(array) != 2 ||
        PyArray_DIM(array, 1) != 4) {
        PyErr_Format(PyExc_ValueError, "%s must have dtype uint8 and shape (n, 4)", name);
        return -1;
    }
    if (writable ? !PyArray_ISCARRAY(array) : !PyArray_ISCARRAY_RO(array)) {
        PyErr_Format(PyExc_ValueError, "%s must be C-contiguous and aligned", name);
        return -1;
    }
    if (writable && !PyArray_ISWRITEABLE(array)) {
        PyErr_Format(PyExc_ValueError, "%s must be writable", name);
        return -1;
    }
    return 0;
}

static PyObject *correct_rgba_into(PyObject *module, PyObject *args) {
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

static PyObject *apply_alpha_scale_into(PyObject *module, PyObject *args) {
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

static void hsl_to_rgb(double hue, double saturation, double lightness,
                       uint8_t *red, uint8_t *green, uint8_t *blue);

static PyObject *render_solid_rgba_into(PyObject *module, PyObject *args) {
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

static PyObject *render_hsl_solid_into(PyObject *module, PyObject *args) {
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

static PyObject *render_halves_rgba_into(PyObject *module, PyObject *args) {
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

static PyObject *render_red_sweep_into(PyObject *module, PyObject *args) {
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

static PyObject *render_red_chase_into(PyObject *module, PyObject *args) {
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

static PyObject *render_drop_explosion_into(PyObject *module, PyObject *args) {
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

static PyObject *render_fire_into(PyObject *module, PyObject *args) {
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
    if (PyArray_TYPE(heat_array) != NPY_FLOAT32 || PyArray_NDIM(heat_array) != 1 ||
        PyArray_TYPE(next_array) != NPY_FLOAT32 || PyArray_NDIM(next_array) != 1 ||
        PyArray_DIM(heat_array, 0) != count || PyArray_DIM(next_array, 0) != count ||
        !PyArray_ISCARRAY(heat_array) || !PyArray_ISCARRAY(next_array)) {
        PyErr_SetString(PyExc_ValueError, "heat buffers must be contiguous float32 vectors matching destination");
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

static PyObject *render_color_wipe_into(PyObject *module, PyObject *args) {
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

static PyObject *composite_screen_rgba_into(PyObject *module, PyObject *args) {
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

static int validate_fixed_array(PyArrayObject *array, int type, int dimensions,
                                npy_intp first, npy_intp second,
                                const char *name) {
    if (PyArray_TYPE(array) != type || PyArray_NDIM(array) != dimensions ||
        PyArray_DIM(array, 0) != first ||
        (dimensions == 2 && PyArray_DIM(array, 1) != second) ||
        !PyArray_ISCARRAY_RO(array)) {
        PyErr_Format(PyExc_ValueError, "%s has an invalid dtype, shape, or layout", name);
        return -1;
    }
    return 0;
}

static PyObject *render_tunnel_rings_into(PyObject *module, PyObject *args) {
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

static void hsl_to_rgb(double hue, double saturation, double lightness,
                       uint8_t *red, uint8_t *green, uint8_t *blue) {
    hue = fmod(hue, 360.0);
    if (hue < 0.0)
        hue += 360.0;
    const double h = hue / 360.0;
    const double s = fmin(fmax(saturation, 0.0), 100.0) / 100.0;
    const double l = fmin(fmax(lightness, 0.0), 100.0) / 100.0;
    if (s == 0.0) {
        const uint8_t gray = (uint8_t)(l * 255.0);
        *red = gray;
        *green = gray;
        *blue = gray;
        return;
    }
    const double q = l < 0.5 ? l * (1.0 + s) : l + s - l * s;
    const double p = 2.0 * l - q;
    const double positions[3] = {h + 1.0 / 3.0, h, h - 1.0 / 3.0};
    uint8_t *channels[3] = {red, green, blue};
    for (size_t channel = 0; channel < 3u; channel++) {
        double t = positions[channel];
        if (t < 0.0)
            t += 1.0;
        else if (t > 1.0)
            t -= 1.0;
        double value;
        if (t < 1.0 / 6.0)
            value = p + (q - p) * 6.0 * t;
        else if (t < 1.0 / 2.0)
            value = q;
        else if (t < 2.0 / 3.0)
            value = p + (q - p) * 6.0 * (2.0 / 3.0 - t);
        else
            value = p;
        *channels[channel] = (uint8_t)fmin(fmax(value * 255.0, 0.0), 255.0);
    }
}

static void map_palette(uint8_t *red, uint8_t *green, uint8_t *blue, int palette) {
    if (palette == 0 || palette == 3)
        return;
    const double r = (double)*red / 255.0;
    const double g = (double)*green / 255.0;
    const double b = (double)*blue / 255.0;
    const double maximum = fmax(r, fmax(g, b));
    const double minimum = fmin(r, fmin(g, b));
    const double delta = maximum - minimum;
    double hue = 0.0;
    double saturation = maximum == 0.0 ? 0.0 : delta / maximum;
    if (delta != 0.0) {
        const double red_component = (maximum - r) / delta;
        const double green_component = (maximum - g) / delta;
        const double blue_component = (maximum - b) / delta;
        if (maximum == r)
            hue = blue_component - green_component;
        else if (maximum == g)
            hue = 2.0 + red_component - blue_component;
        else
            hue = 4.0 + green_component - red_component;
        hue /= 6.0;
        if (hue < 0.0)
            hue += 1.0;
        hue *= 360.0;
    }
    const int selected = (int)(hue / (palette == 1 ? 45.0 : 55.0));
    static const int neon_hues[7] = {300, 160, 52, 206, 12, 268, 132};
    static const int fire_hues[5] = {0, 10, 22, 35, 48};
    const int mapped_hue = palette == 1
        ? neon_hues[selected % 7]
        : fire_hues[selected % 5];
    const int mapped_saturation = (int)(saturation * 100.0);
    const int mapped_lightness = (int)(36.0 + maximum * 24.0);
    hsl_to_rgb((double)mapped_hue, (double)mapped_saturation,
               (double)mapped_lightness, red, green, blue);
}

static void palette_hsl_to_rgb(double hue, double saturation, double lightness,
                               int palette, unsigned int custom_rgb,
                               uint8_t *red, uint8_t *green, uint8_t *blue) {
    if (palette == 3) {
        *red = (uint8_t)((custom_rgb >> 16u) & 0xFFu);
        *green = (uint8_t)((custom_rgb >> 8u) & 0xFFu);
        *blue = (uint8_t)(custom_rgb & 0xFFu);
        return;
    }
    hsl_to_rgb(hue, saturation, lightness, red, green, blue);
    map_palette(red, green, blue, palette);
}

static void catalog_palette_hsl_to_rgb(double hue, double saturation,
                                       double lightness, int palette,
                                       unsigned int custom_rgb, uint8_t *red,
                                       uint8_t *green, uint8_t *blue) {
    static const int neon_hues[7] = {300, 160, 52, 206, 12, 268, 132};
    static const int fire_hues[5] = {0, 10, 22, 35, 48};
    if (palette == 3) {
        *red = (uint8_t)(custom_rgb >> 16u);
        *green = (uint8_t)(custom_rgb >> 8u);
        *blue = (uint8_t)custom_rgb;
        return;
    }
    if (palette == 1)
        hue = (double)neon_hues[((int)floor(hue / 45.0) % 7 + 7) % 7];
    else if (palette == 2)
        hue = (double)fire_hues[((int)floor(hue / 55.0) % 5 + 5) % 5];
    hsl_to_rgb(hue, saturation, lightness, red, green, blue);
}

static PyObject *render_rainbow_pattern_into(PyObject *module, PyObject *args) {
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
            red = green = blue = 0u;
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

static PyObject *render_theater_chase_into(PyObject *module, PyObject *args) {
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

static PyObject *render_laser_sweep_into(PyObject *module, PyObject *args) {
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

static PyObject *render_sinelon_into(PyObject *module, PyObject *args) {
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

static PyObject *render_peak_mirror_into(PyObject *module, PyObject *args) {
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

static PyObject *render_bouncing_ball_into(PyObject *module, PyObject *args) {
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

static PyObject *render_wave_into(PyObject *module, PyObject *args) {
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

static PyObject *render_juggle_into(PyObject *module, PyObject *args) {
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

static PyObject *render_confetti_into(PyObject *module, PyObject *args) {
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
    if (PyArray_TYPE(intensity_array) != NPY_FLOAT32 || PyArray_NDIM(intensity_array) != 1 ||
        !PyArray_ISCARRAY(intensity_array) ||
        PyArray_DIM(intensity_array, 0) != PyArray_DIM(destination, 0)) {
        PyErr_SetString(PyExc_ValueError, "intensities must be contiguous float32 with one value per pixel");
        return NULL;
    }
    if ((audio_mode != 0 && audio_mode != 1) || !isfinite(fade) || fade < 0.0 ||
        fade > 1.0 || !isfinite(spread) || spread < 0.0 || spread > 1.0 ||
        !isfinite(trigger_rate) || trigger_rate < 0.0 || density < 0 ||
        !isfinite(addition) || addition < 0.0 || !isfinite(accent) ||
        !isfinite(high) || !isfinite(bloom) || !isfinite(brightness) ||
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
            if (PyErr_Occurred() || !isfinite(bands[band])) {
                Py_DECREF(sequence);
                if (!PyErr_Occurred())
                    PyErr_SetString(PyExc_ValueError, "bands must be finite values");
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
    const npy_intp center = count > 0 ? (npy_intp)(((uint64_t)frame * 37u) % (uint64_t)count) : 0;
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

static PyObject *render_glow_rush_into(PyObject *module, PyObject *args) {
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
        main_light < 0 || main_light > 100 || !isfinite(glow_alpha) || glow_alpha < 0.0 ||
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

static PyObject *render_dual_comet_into(PyObject *module, PyObject *args) {
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

static PyObject *render_shockwave_into(PyObject *module, PyObject *args) {
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
        !isfinite(mid) || !isfinite(brightness) || brightness < 0.0 || brightness > 1.0 ||
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
            if (PyErr_Occurred() || !isfinite(bands[band])) {
                Py_DECREF(sequence);
                if (!PyErr_Occurred())
                    PyErr_SetString(PyExc_ValueError, "bands must be finite values");
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

static PyObject *render_aurora_into(PyObject *module, PyObject *args) {
    (void)module;
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
        !isfinite(speed) || !isfinite(low) || !isfinite(mid) || !isfinite(high) ||
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
            if (PyErr_Occurred() || !isfinite(bands[band])) {
                Py_DECREF(sequence);
                if (!PyErr_Occurred())
                    PyErr_SetString(PyExc_ValueError, "bands must be finite values");
                return NULL;
            }
        }
        Py_DECREF(sequence);
    }
    const npy_intp count = PyArray_DIM(destination, 0);
    uint8_t *pixels = (uint8_t *)PyArray_DATA(destination);
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
        pixels[offset + 3u] = (uint8_t)(alpha * brightness * 255.0);
    }
    Py_RETURN_NONE;
}

static PyObject *render_comet_storm_into(PyObject *module, PyObject *args) {
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
    if (PyArray_TYPE(geometry) != NPY_DOUBLE || PyArray_NDIM(geometry) != 2 ||
        PyArray_DIM(geometry, 1) != 4 || !PyArray_ISCARRAY_RO(geometry)) {
        PyErr_SetString(PyExc_ValueError, "geometry must be a contiguous float64 array of shape (n, 4)");
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

static PyObject *render_twinkle_into(PyObject *module, PyObject *args) {
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
    if (PyArray_TYPE(levels) != NPY_FLOAT32 || PyArray_NDIM(levels) != 1 ||
        PyArray_DIM(levels, 0) != count || !PyArray_ISCARRAY_RO(levels) ||
        PyArray_TYPE(hues) != NPY_INT16 || PyArray_NDIM(hues) != 1 ||
        PyArray_DIM(hues, 0) != count || !PyArray_ISCARRAY_RO(hues)) {
        PyErr_SetString(PyExc_ValueError, "levels and hues must be contiguous arrays matching destination length");
        return NULL;
    }
    if (radius < 0 || !isfinite(bloom) || !isfinite(brightness) ||
        brightness < 0.0 || brightness > 1.0 || palette < 0 || palette > 3 ||
        custom_rgb > 0xFFFFFFu) {
        PyErr_SetString(PyExc_ValueError, "invalid Twinkle render parameters");
        return NULL;
    }
    const float *level_values = (const float *)PyArray_DATA(levels);
    const int16_t *hue_values = (const int16_t *)PyArray_DATA(hues);
    uint8_t *output = (uint8_t *)PyArray_DATA(destination);
    for (npy_intp index = 0; index < count; index++) {
        const double position = count <= 1 ? 0.0 : (double)index / (double)(count - 1);
        const double drift = sin((double)frame * 0.018 + position * 4.2) * 0.5 + 0.5;
        double level = (double)level_values[index];
        if (radius > 1) {
            const npy_intp left = index > radius ? index - radius : 0;
            const npy_intp right = index + radius + 1 < count ? index + radius + 1 : count;
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

static PyObject *render_spectrum_flash_into(PyObject *module, PyObject *args) {
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
    if (!isfinite(width) || !isfinite(hue_phase) || !isfinite(flash) ||
        !isfinite(brightness) || width < 0.0 || brightness < 0.0 ||
        brightness > 1.0 || palette < 0 || palette > 3 ||
        custom_rgb > 0xFFFFFFu) {
        Py_DECREF(bands);
        PyErr_SetString(PyExc_ValueError, "invalid SpectrumFlash render parameters");
        return NULL;
    }
    double band_values[8];
    for (Py_ssize_t i = 0; i < 8; i++) {
        band_values[i] = PyFloat_AsDouble(PySequence_Fast_GET_ITEM(bands, i));
        if (PyErr_Occurred()) {
            Py_DECREF(bands);
            return NULL;
        }
    }
    Py_DECREF(bands);

    uint8_t *output = (uint8_t *)PyArray_DATA(destination);
    const npy_intp count = PyArray_DIM(destination, 0);
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
            const int seed = (int)(hue_phase / 8.0) + (int)index;
            static const int warm_hues[4] = {0, 20, 38, 320};
            hue = warm_hues[((seed % 4) + 4) % 4];
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

static PyObject *render_pulse_into(PyObject *module, PyObject *args) {
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
    uint8_t *pixels = (uint8_t *)PyArray_DATA(destination);
    for (npy_intp index = 0; index < count; index++) {
        const double spatial = cos(phase * 6.283185307179586 +
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
                selected_hue = (double)neon_hues[(int)(hue / 45.0) % 7];
            else if (palette == 2)
                selected_hue = (double)fire_hues[(int)(hue / 55.0) % 5];
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

static PyObject *render_comet_into(PyObject *module, PyObject *args) {
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
        !isfinite(shimmer) || !isfinite(hue) || !isfinite(intensity) ||
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
            if (palette == 1)
                selected_hue = (double)neon_hues[(int)(hue / 45.0) % 7];
            else if (palette == 2) {
                static const int fire_hues[5] = {0, 10, 22, 35, 48};
                selected_hue = (double)fire_hues[(int)(hue / 55.0) % 5];
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

static PyObject *render_dance_floor_into(PyObject *module, PyObject *args) {
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
        !isfinite(mid) || !isfinite(high) || !isfinite(drive) ||
        !isfinite(beat_boost) || !isfinite(brightness) || brightness < 0.0 ||
        brightness > 1.0 || palette < 0 || palette > 3 ||
        custom_rgb > 0xFFFFFFu) {
        PyErr_SetString(PyExc_ValueError, "invalid DanceFloor render parameters");
        return NULL;
    }
    const npy_intp count = PyArray_DIM(destination, 0);
    const int segments = count > 0
        ? (int)((count + segment_width - 1) / segment_width)
        : 1;
    uint8_t *pixels = (uint8_t *)PyArray_DATA(destination);
    for (npy_intp index = 0; index < count; index++) {
        const int segment = (int)index / segment_width;
        int hue, lightness;
        double alpha = 1.0;
        if (audio_mode) {
            hue = (segment * 40 + (int)hue_shift) % 256;
            const double phase = (double)segment * (0.4 + frequency / 50.0) +
                                 (double)frame * 0.08;
            const double breathing = (sin(phase) * 0.5 + 0.5);
            lightness = (int)(30.0 + mid * 20.0 +
                              (double)segment / (double)segments * high * 25.0 +
                              beat_boost * 25.0 + breathing * 4.0);
            alpha = fmin(0.6 + drive * 0.4 + beat_boost * 0.3, 1.0);
        } else {
            hue = (segment * 40 + frame * 2) % 256;
            const double phase = (double)segment * (0.4 + frequency / 50.0) +
                                 (double)frame * 0.08;
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

static PyObject *render_disco_sparkle_into(PyObject *module, PyObject *args) {
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
    if (PyArray_TYPE(intensities) != NPY_FLOAT32 || PyArray_NDIM(intensities) != 1 ||
        PyArray_DIM(intensities, 0) != PyArray_DIM(destination, 0) ||
        !PyArray_IS_C_CONTIGUOUS(intensities) || !PyArray_ISWRITEABLE(intensities)) {
        PyErr_SetString(PyExc_ValueError,
                        "intensities must be a writable contiguous float32 array matching destination");
        return NULL;
    }
    if ((audio_mode != 0 && audio_mode != 1) || density < 0 || density > 100 ||
        (accent_trigger != 0 && accent_trigger != 1) || !isfinite(decay) ||
        !isfinite(variation) || !isfinite(hue_rate) || !isfinite(pulse) ||
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
        const int variation_value = (int)(((int)(seed % UINT64_C(21)) - 10) * variation);
        const int animated_frame = audio_mode ? (int)((double)frame * hue_rate) : (int)frame * 3;
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

static PyObject *render_club_flash_into(PyObject *module, PyObject *args) {
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
        const int hue = warm_hues[(uint64_t)hue_seed % UINT64_C(4)];
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
    const int base_hue = club_hues[(uint64_t)hue_seed % UINT64_C(7)];
    uint8_t base_red, base_green, base_blue;
    palette_hsl_to_rgb((double)base_hue, 100.0, 58.0, palette,
                       custom_rgb, &base_red, &base_green, &base_blue);
    const int mirror_enabled = flash > 0.6 && count > 6;
    const double mirror_alpha = fmin(1.0, 0.1 + flash * 0.7);
    for (int offset = 0; offset < segment_width; offset++) {
        const npy_intp index = (segment_start + offset) % count;
        const size_t pixel_offset = (size_t)index * 4u;
        pixels[pixel_offset] = base_red;
        pixels[pixel_offset + 1u] = base_green;
        pixels[pixel_offset + 2u] = base_blue;
        pixels[pixel_offset + 3u] = (uint8_t)(base_alpha * brightness * 255.0);
        if (mirror_enabled) {
            const npy_intp mirror = (count - 1 - index) > 0 ? count - 1 - index : 0;
            const int warm_hue = warm_hues[((uint64_t)hue_seed + (uint64_t)offset) % UINT64_C(4)];
            uint8_t red, green, blue;
            palette_hsl_to_rgb((double)warm_hue, 100.0, 60.0,
                               palette, custom_rgb, &red, &green, &blue);
            const size_t mirror_offset = (size_t)mirror * 4u;
            pixels[mirror_offset] = red;
            pixels[mirror_offset + 1u] = green;
            pixels[mirror_offset + 2u] = blue;
            pixels[mirror_offset + 3u] = (uint8_t)(mirror_alpha * brightness * 255.0);
        }
    }
    Py_RETURN_NONE;
}

static PyObject *render_beat_wave_into(PyObject *module, PyObject *args) {
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
    if (PyArray_TYPE(waves) != NPY_FLOAT64 || PyArray_NDIM(waves) != 2 ||
        PyArray_DIM(waves, 0) != 8 || PyArray_DIM(waves, 1) != 2 ||
        !PyArray_IS_C_CONTIGUOUS(waves)) {
        PyErr_SetString(PyExc_ValueError,
                        "waves must be a contiguous float64 array with shape (8, 2)");
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

static PyObject *render_beat_explosion_into(PyObject *module, PyObject *args) {
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
        !isfinite(mid) || !isfinite(high) || !isfinite(brightness) ||
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
            hue = (10 + (int)(low * 30.0) + frame * 2) % 256;
            lightness = (int)(50.0 + mid * 15.0 + hit * 15.0);
        } else {
            const double ring = fmax(1.0 - fabs(dist - radius) / width, 0.0);
            alpha = ring * ((frame % 30) < 3 ? 0.9 : 0.0);
            hue = (frame * 4 + (int)index * 2) % 256;
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

static void write_transparent_palette_pixel(uint8_t *pixels, size_t offset,
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

static PyObject *render_firework_burst_into(PyObject *module, PyObject *args) {
    (void)module;
    PyArrayObject *destination, *phases, *bursts;
    int phase_count, burst_count, frame, audio_mode, hue, palette;
    double center, fade_alpha, glow, mid, high, brightness;
    PyObject *bands_object;
    unsigned int custom_rgb;
    if (!PyArg_ParseTuple(args, "O!O!O!iiiiddddddOiiI", &PyArray_Type,
                          &destination, &PyArray_Type, &phases, &PyArray_Type,
                          &bursts, &phase_count, &burst_count, &frame,
                          &audio_mode, &center, &fade_alpha, &glow, &mid,
                          &high, &brightness, &bands_object, &hue, &palette,
                          &custom_rgb))
        return NULL;
    if (validate_rgba(destination, "destination", 1) < 0)
        return NULL;
    if (PyArray_TYPE(phases) != NPY_FLOAT64 || PyArray_NDIM(phases) != 1 ||
        PyArray_DIM(phases, 0) != 12 || !PyArray_IS_C_CONTIGUOUS(phases) ||
        PyArray_TYPE(bursts) != NPY_FLOAT64 || PyArray_NDIM(bursts) != 2 ||
        PyArray_DIM(bursts, 0) != 12 || PyArray_DIM(bursts, 1) != 3 ||
        !PyArray_IS_C_CONTIGUOUS(bursts)) {
        PyErr_SetString(PyExc_ValueError,
                        "FireworkBurst scratch arrays have invalid shapes or layouts");
        return NULL;
    }
    if (phase_count < 0 || phase_count > 12 || burst_count < 0 || burst_count > 12 ||
        (audio_mode != 0 && audio_mode != 1) || !isfinite(center) || center < 0.0 ||
        !isfinite(fade_alpha) || fade_alpha < 0.0 || fade_alpha > 1.0 ||
        !isfinite(glow) || glow < 0.0 || !isfinite(mid) || !isfinite(high) ||
        !isfinite(brightness) || brightness < 0.0 || brightness > 1.0 ||
        hue < 0 || hue > 255 || palette < 0 || palette > 3 ||
        custom_rgb > 0xFFFFFFu) {
        PyErr_SetString(PyExc_ValueError, "invalid FireworkBurst render parameters");
        return NULL;
    }
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
            if (PyErr_Occurred()) {
                Py_DECREF(sequence);
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
            const int burst_id = (int)(frame / 36);
            for (int phase_index = 0; phase_index < phase_count; phase_index++) {
                const double phase = phase_values[phase_index];
                if (dist <= phase) {
                    const double sparkle_dist = (phase - dist) / fmax(phase, 1.0);
                    const double candidate = fmax(1.0 - sparkle_dist * 1.5, 0.0);
                    int offset = ((int)index * 7 + burst_id * 13) % 5;
                    if (offset < 0)
                        offset += 5;
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
                    const int particle = ((int)index * 7 + (int)(phase * 10.0) * 13) % 7;
                    wave_alpha = fmax(wave_alpha + (double)(particle - 3) * 0.06, 0.0);
                    double band_sparkle = 0.0;
                    if (band_count > 0) {
                        const Py_ssize_t band_index = length <= 1
                            ? 0
                            : fmin((double)(index * band_count / length),
                                   (double)(band_count - 1));
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

static PyObject *render_color_burst_into(PyObject *module, PyObject *args) {
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
    if (PyArray_TYPE(bursts) != NPY_FLOAT64 || PyArray_NDIM(bursts) != 2 ||
        PyArray_DIM(bursts, 0) != 6 || PyArray_DIM(bursts, 1) != 5 ||
        !PyArray_IS_C_CONTIGUOUS(bursts)) {
        PyErr_SetString(PyExc_ValueError,
                        "ColorBurst scratch must be contiguous float64 with shape (6, 5)");
        return NULL;
    }
    if (burst_count < 0 || burst_count > 6 || !isfinite(brightness) ||
        brightness < 0.0 || brightness > 1.0 || palette < 0 || palette > 3 ||
        custom_rgb > 0xFFFFFFu) {
        PyErr_SetString(PyExc_ValueError, "invalid ColorBurst render parameters");
        return NULL;
    }
    const double *state = (const double *)PyArray_DATA(bursts);
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
            int hue_index = (hue_seed + (int)index) % 7;
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

static PyObject *render_plasma_rave_into(PyObject *module, PyObject *args) {
    (void)module;
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
        alpha_extra < 0.0 || alpha_extra > 1.0 || brightness < 0.0 ||
        brightness > 1.0 || palette < 0 || palette > 3 ||
        (clamp_alpha != 0 && clamp_alpha != 1) ||
        (hue_base_before_truncation != 0 && hue_base_before_truncation != 1) ||
        custom_rgb > 0xFFFFFFu) {
        PyErr_SetString(PyExc_ValueError, "invalid PlasmaRave render parameters");
        return NULL;
    }
    const npy_intp length = PyArray_DIM(destination, 0);
    uint8_t *pixels = (uint8_t *)PyArray_DATA(destination);
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
        if (alpha > 1.0)
            alpha = 1.0;
        pixels[offset + 3u] = (uint8_t)(alpha * 255.0);
    }
    Py_RETURN_NONE;
}

static PyObject *render_beat_ripple_into(PyObject *module, PyObject *args) {
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

static PyObject *render_neon_confetti_into(PyObject *module, PyObject *args) {
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
    if (PyArray_TYPE(levels) != NPY_FLOAT32 || PyArray_NDIM(levels) != 1 ||
        PyArray_DIM(levels, 0) != length || !PyArray_IS_C_CONTIGUOUS(levels) ||
        !PyArray_ISWRITEABLE(levels) || PyArray_TYPE(hues) != NPY_INT16 ||
        PyArray_NDIM(hues) != 1 || PyArray_DIM(hues, 0) != length ||
        !PyArray_IS_C_CONTIGUOUS(hues) || !PyArray_ISWRITEABLE(hues)) {
        PyErr_SetString(PyExc_ValueError,
                        "NeonConfetti state arrays must match the destination");
        return NULL;
    }
    if (!isfinite(fade_multiplier) || fade_multiplier < 0.0 ||
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
    const double center = (double)((frame * 53) % safe_length);
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

static PyObject *render_strobe_chase_into(PyObject *module, PyObject *args) {
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
        const int warm_index = (int)((hue + frame) % 4);
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
        const int hue_index = (hue + (int)index * 11) % 7;
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

static PyObject *render_rave_scanner_into(PyObject *module, PyObject *args) {
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
        const int color_index = (hue + (int)(distance * 18.0)) % 7;
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

static PyObject *render_center_burst_into(PyObject *module, PyObject *args) {
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
        const size_t hue_index = (size_t)((hue_seed + index) % 7);
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

static PyObject *render_dual_laser_into(PyObject *module, PyObject *args) {
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

static PyObject *render_mirror_flash_into(PyObject *module, PyObject *args) {
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
        const Py_ssize_t hue = hue_seed + (alternate ? index * 13 : -index * 9);
        const double alpha = fmin(1.0, 0.14 + intensity * 0.86);
        const npy_intp mirror = length - 1 - index > 0 ? length - 1 - index : 0;
        uint8_t red, green, blue;
        Py_ssize_t color_hue = hue % 7;
        if (color_hue < 0)
            color_hue += 7;
        palette_hsl_to_rgb((double)club_hues[color_hue], 100.0, 62.0,
                           palette, custom_rgb, &red, &green, &blue);
        size_t offset = (size_t)index * 4u;
        pixels[offset] = red;
        pixels[offset + 1u] = green;
        pixels[offset + 2u] = blue;
        pixels[offset + 3u] = (uint8_t)(alpha * brightness * 255.0);
        if (mirror != index) {
            if (alternate) {
                Py_ssize_t warm_index = (hue + 11) % 4;
                if (warm_index < 0)
                    warm_index += 4;
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

static PyObject *render_drop_wave_into(PyObject *module, PyObject *args) {
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
            Py_ssize_t color_index = (hue_seed + index) % 4;
            if (color_index < 0)
                color_index += 4;
            hue = warm_hues[color_index];
            lightness = 60;
        } else if (charge > 0.35) {
            const double pre = fmax(0.0, 1.0 - distance / fmax(center, 1.0));
            alpha = fmin(1.0, pre * (0.08 + charge * 0.2));
            if (alpha <= 0.0)
                continue;
            Py_ssize_t color_index = (hue_seed + index) % 7;
            if (color_index < 0)
                color_index += 7;
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

static PyObject *render_disco_comet_into(PyObject *module, PyObject *args) {
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
        Py_ssize_t club_index = ((Py_ssize_t)hue + index * 5) % 7;
        if (club_index < 0)
            club_index += 7;
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
            const Py_ssize_t neon_index = ((Py_ssize_t)hue + (Py_ssize_t)sparkle_seed) % 7;
            const double sparkle_alpha = fmin(1.0, 0.65 + high * 0.35);
            palette_hsl_to_rgb((double)neon_hues[neon_index], 100.0, 62.0,
                               palette, custom_rgb, &red, &green, &blue);
            offset = (size_t)index * 4u;
            pixels[offset] = red;
            pixels[offset + 1u] = green;
            pixels[offset + 2u] = blue;
            pixels[offset + 3u] = (uint8_t)(sparkle_alpha * brightness * 255.0);
            if (index + 1 < length && (sparkle_seed / 7u) % 2u == 0u) {
                const Py_ssize_t neighbor_index =
                    ((Py_ssize_t)hue + (Py_ssize_t)sparkle_seed + 13) % 7;
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

static PyObject *render_pixel_explosion_into(PyObject *module, PyObject *args) {
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
    if (PyArray_TYPE(bursts) != NPY_FLOAT64 || PyArray_NDIM(bursts) != 2 ||
        PyArray_DIM(bursts, 1) != 4 || burst_count < 0 ||
        burst_count > PyArray_DIM(bursts, 0) ||
        !PyArray_IS_C_CONTIGUOUS(bursts)) {
        PyErr_SetString(PyExc_ValueError,
                        "PixelExplosion bursts must be contiguous float64 rows");
        return NULL;
    }
    if (!isfinite(width) || width <= 0.0 || !isfinite(brightness) ||
        brightness < 0.0 || brightness > 1.0 || palette < 0 || palette > 3 ||
        custom_rgb > 0xFFFFFFu) {
        PyErr_SetString(PyExc_ValueError, "invalid PixelExplosion render parameters");
        return NULL;
    }
    const double *state = (const double *)PyArray_DATA(bursts);
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

static PyObject *render_lightning_strike_into(PyObject *module, PyObject *args) {
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
        all_flash < 0.0 || !isfinite(brightness) || brightness < 0.0 ||
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

static PyObject *render_neon_storm_into(PyObject *module, PyObject *args) {
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
    if (PyArray_TYPE(levels) != NPY_FLOAT32 || PyArray_NDIM(levels) != 1 ||
        PyArray_DIM(levels, 0) != count || !PyArray_ISCARRAY_RO(levels) ||
        PyArray_TYPE(hues) != NPY_INT16 || PyArray_NDIM(hues) != 1 ||
        PyArray_DIM(hues, 0) != count || !PyArray_ISCARRAY_RO(hues) ||
        PyArray_TYPE(positions) != NPY_FLOAT64 || PyArray_NDIM(positions) != 1 ||
        !PyArray_ISCARRAY_RO(positions) || !isfinite(drive) ||
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
        int selected = (p >= streak ? particle_hue[index] : (int)streak_seed) % 5;
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

static PyObject *render_electric_storm_into(PyObject *module, PyObject *args) {
    (void)module;
    PyArrayObject *destination, *geometry;
    double flash_alpha, brightness;
    int palette;
    unsigned int custom_rgb;
    if (!PyArg_ParseTuple(args, "O!O!ddiI", &PyArray_Type, &destination,
                          &PyArray_Type, &geometry, &flash_alpha, &brightness,
                          &palette, &custom_rgb)) return NULL;
    if (validate_rgba(destination, "destination", 1) < 0) return NULL;
    if (PyArray_TYPE(geometry) != NPY_FLOAT64 || PyArray_NDIM(geometry) != 2 ||
        PyArray_DIM(geometry, 1) != 4 || !PyArray_ISCARRAY_RO(geometry) ||
        !isfinite(flash_alpha) || flash_alpha < 0.0 || flash_alpha > 1.0 ||
        !isfinite(brightness) || brightness < 0.0 || brightness > 1.0 ||
        palette < 0 || palette > 3 || custom_rgb > 0xFFFFFFu) {
        PyErr_SetString(PyExc_ValueError, "invalid ElectricStorm buffers or parameters");
        return NULL;
    }
    const npy_intp count = PyArray_DIM(destination, 0);
    const npy_intp streak_count = PyArray_DIM(geometry, 0);
    const double *streaks = (const double *)PyArray_DATA(geometry);
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
        const int length = (int)streaks[s * 4 + 1];
        const double hue = streaks[s * 4 + 2];
        const double alpha = streaks[s * 4 + 3];
        uint8_t red, green, blue;
        catalog_palette_hsl_to_rgb(hue, 90.0, 75.0, palette, custom_rgb, &red, &green, &blue);
        for (int j = 0; j < length; j++) {
            const int pixel = (int)pos - j;
            if (pixel < 0 || pixel >= count) continue;
            const double intensity = 1.0 - (double)j / (double)(length > 1 ? length : 1);
            const size_t offset = (size_t)pixel * 4u;
            pixels[offset] = red; pixels[offset + 1u] = green; pixels[offset + 2u] = blue;
            pixels[offset + 3u] = (uint8_t)(intensity * alpha * brightness * 255.0);
        }
    }
    Py_RETURN_NONE;
}

static PyMethodDef module_methods[] = {
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
    {"render_confetti_into", render_confetti_into, METH_VARARGS,
     "Advance Confetti intensity state and render particles into an RGBA frame."},
    {"render_glow_rush_into", render_glow_rush_into, METH_VARARGS,
     "Render GlowRush tail and glow updates without clearing untouched pixels."},
    {"render_dual_comet_into", render_dual_comet_into, METH_VARARGS,
     "Render overlapping DualComet trails into an RGBA frame."},
    {"render_shockwave_into", render_shockwave_into, METH_VARARGS,
     "Render a radial Shockwave ring and ambient field into an RGBA frame."},
    {"render_aurora_into", render_aurora_into, METH_VARARGS,
     "Render Aurora curtain waves with optional audio band modulation."},
    {"render_comet_storm_into", render_comet_storm_into, METH_VARARGS,
     "Render overlapping CometStorm trails into an RGBA frame."},
    {"render_twinkle_into", render_twinkle_into, METH_VARARGS,
     "Render the Twinkle state buffer into an RGBA frame."},
    {"composite_screen_rgba_into", composite_screen_rgba_into, METH_VARARGS,
     "Composite up to two alpha-weighted RGBA overlays using SCREEN blending."},
    {"render_tunnel_rings_into", render_tunnel_rings_into, METH_VARARGS,
     "Render up to two alpha-weighted BeatTunnel rings into an RGBA frame."},
    {"render_spectrum_flash_into", render_spectrum_flash_into, METH_VARARGS,
     "Render SpectrumFlash spectrum and flash pixels into an RGBA frame."},
    {"render_pulse_into", render_pulse_into, METH_VARARGS,
     "Render the spatial Pulse field into an RGBA frame."},
    {"render_comet_into", render_comet_into, METH_VARARGS,
     "Render a Comet trail into an RGBA frame."},
    {"render_dance_floor_into", render_dance_floor_into, METH_VARARGS,
     "Render segmented DanceFloor colors into an RGBA frame."},
    {"render_disco_sparkle_into", render_disco_sparkle_into, METH_VARARGS,
     "Advance DiscoSparkle intensity state and render RGBA pixels."},
    {"render_club_flash_into", render_club_flash_into, METH_VARARGS,
     "Render ClubFlash whole-strip or segmented mirrored output."},
    {"render_beat_wave_into", render_beat_wave_into, METH_VARARGS,
     "Render up to eight audio-triggered BeatWave rings."},
    {"render_beat_explosion_into", render_beat_explosion_into, METH_VARARGS,
     "Render the static or audio-reactive BeatExplosion ring."},
    {"render_firework_burst_into", render_firework_burst_into, METH_VARARGS,
     "Render static FireworkBurst phases or active audio bursts."},
    {"render_color_burst_into", render_color_burst_into, METH_VARARGS,
     "Render up to six ColorBurst rings over an opaque black frame."},
    {"render_plasma_rave_into", render_plasma_rave_into, METH_VARARGS,
     "Render PlasmaRave's static or audio-reactive plasma field."},
    {"render_beat_ripple_into", render_beat_ripple_into, METH_VARARGS,
     "Render BeatRipple's mirrored static or audio-reactive rings."},
    {"render_neon_confetti_into", render_neon_confetti_into, METH_VARARGS,
     "Advance and render NeonConfetti's deterministic particle field."},
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
    {"render_disco_comet_into", render_disco_comet_into, METH_VARARGS,
     "Render DiscoComet's moving trail and deterministic sparkle overlay."},
    {"render_pixel_explosion_into", render_pixel_explosion_into, METH_VARARGS,
     "Render active PixelExplosion rings into a transparent RGBA frame."},
    {"render_lightning_strike_into", render_lightning_strike_into, METH_VARARGS,
     "Render LightningStrike's bolt flash or ambient fade."},
    {"render_neon_storm_into", render_neon_storm_into, METH_VARARGS,
     "Render NeonStorm into a reusable RGBA destination."},
    {"render_electric_storm_into", render_electric_storm_into, METH_VARARGS,
     "Render ElectricStorm flashes and streaks into a reusable RGBA destination."},
    {NULL, NULL, 0, NULL}
};

static struct PyModuleDef module_definition = {
    PyModuleDef_HEAD_INIT,
    .m_name = "lumistripe.render._native",
    .m_doc = "Portable scalar C kernels for RGBA frame processing",
    .m_size = -1,
    .m_methods = module_methods,
};

PyMODINIT_FUNC PyInit__native(void) {
    import_array();
    if (PyErr_Occurred())
        return NULL;
    return PyModule_Create(&module_definition);
}
