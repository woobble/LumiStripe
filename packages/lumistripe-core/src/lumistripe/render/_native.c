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
        if (maximum == r)
            hue = fmod((g - b) / delta, 6.0);
        else if (maximum == g)
            hue = (b - r) / delta + 2.0;
        else
            hue = (r - g) / delta + 4.0;
        hue *= 60.0;
        if (hue < 0.0)
            hue += 360.0;
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

static PyMethodDef module_methods[] = {
    {"correct_rgba_into", correct_rgba_into, METH_VARARGS,
     "Apply integer RGB correction into a contiguous RGBA destination."},
    {"apply_alpha_scale_into", apply_alpha_scale_into, METH_VARARGS,
     "Copy RGBA pixels and floor-scale alpha into reusable destinations."},
    {"render_solid_rgba_into", render_solid_rgba_into, METH_VARARGS,
     "Fill an RGBA destination with one precomputed pixel."},
    {"render_halves_rgba_into", render_halves_rgba_into, METH_VARARGS,
     "Fill the two halves of an RGBA destination with separate pixels."},
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
