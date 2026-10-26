#include "native_common.h"
#include <stddef.h>
int validate_rgba(PyArrayObject *array, const char *name, int writable) {
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

/* Arrays accepted here are C-contiguous, so their byte ranges are exact. */

int arrays_overlap(const PyArrayObject *left, const PyArrayObject *right) {
    const uintptr_t left_start = (uintptr_t)PyArray_DATA((PyArrayObject *)left);
    const uintptr_t right_start = (uintptr_t)PyArray_DATA((PyArrayObject *)right);
    const uintptr_t left_size = (uintptr_t)PyArray_NBYTES((PyArrayObject *)left);
    const uintptr_t right_size = (uintptr_t)PyArray_NBYTES((PyArrayObject *)right);
    if (left_size == 0u || right_size == 0u)
        return 0;
    return left_start <= right_start
        ? right_start - left_start < left_size
        : left_start - right_start < right_size;
}

int reject_partial_overlap(const PyArrayObject *left,
                                  const PyArrayObject *right,
                                  const char *left_name,
                                  const char *right_name) {
    if (arrays_overlap(left, right) &&
        PyArray_DATA((PyArrayObject *)left) != PyArray_DATA((PyArrayObject *)right)) {
        PyErr_Format(PyExc_ValueError,
                     "%s and %s must not partially overlap", left_name, right_name);
        return -1;
    }
    return 0;
}

int validate_fixed_array(PyArrayObject *array, int type, int dimensions,
                                npy_intp first, npy_intp second,
                                const char *name) {
    if (PyArray_TYPE(array) != type || PyArray_NDIM(array) != dimensions ||
        PyArray_DIM(array, 0) != first ||
        (dimensions == 2 && PyArray_DIM(array, 1) != second) ||
        !PyArray_ISCARRAY_RO(array)) {
        PyErr_Format(PyExc_ValueError, "%s has an invalid dtype, shape, or layout", name);
        return -1;
    }
    if (type == NPY_DOUBLE || type == NPY_FLOAT) {
        const npy_intp count = PyArray_SIZE(array);
        const double *values = (const double *)PyArray_DATA(array);
        if (type == NPY_DOUBLE) {
            for (npy_intp i = 0; i < count; i++) {
                if (!isfinite(values[i])) {
                    PyErr_Format(PyExc_ValueError, "%s values must be finite", name);
                    return -1;
                }
            }
        } else {
            const float *float_values = (const float *)PyArray_DATA(array);
            for (npy_intp i = 0; i < count; i++) {
                if (!isfinite(float_values[i])) {
                    PyErr_Format(PyExc_ValueError, "%s values must be finite", name);
                    return -1;
                }
            }
        }
    }
    return 0;
}

void hsl_to_rgb(double hue, double saturation, double lightness,
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

void map_palette(uint8_t *red, uint8_t *green, uint8_t *blue, int palette) {
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

void palette_hsl_to_rgb(double hue, double saturation, double lightness,
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

void catalog_palette_hsl_to_rgb(double hue, double saturation,
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
