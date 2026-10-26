#include "native_common.h"
#include <stddef.h>
int validate_rgba(PyArrayObject *array, const char *name, int writable) {
    const npy_intp shape[2] = {-1, 4};
    return validate_array(array, NPY_UINT8, 2, shape, writable,
                          -INFINITY, INFINITY, name);
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
    const int exact_compatible =
        PyArray_DATA((PyArrayObject *)left) == PyArray_DATA((PyArrayObject *)right) &&
        PyArray_TYPE((PyArrayObject *)left) == PyArray_TYPE((PyArrayObject *)right) &&
        PyArray_NDIM((PyArrayObject *)left) == PyArray_NDIM((PyArrayObject *)right) &&
        PyArray_NBYTES((PyArrayObject *)left) == PyArray_NBYTES((PyArrayObject *)right);
    int same_shape = exact_compatible;
    for (int dimension = 0; same_shape &&
         dimension < PyArray_NDIM((PyArrayObject *)left); dimension++) {
        same_shape = PyArray_DIM((PyArrayObject *)left, dimension) ==
                     PyArray_DIM((PyArrayObject *)right, dimension);
    }
    if (arrays_overlap(left, right) && !same_shape) {
        PyErr_Format(PyExc_ValueError,
                     "%s and %s overlap unsafely", left_name, right_name);
        return -1;
    }
    return 0;
}

int reject_any_overlap(const PyArrayObject *left, const PyArrayObject *right,
                       const char *left_name, const char *right_name) {
    if (arrays_overlap(left, right)) {
        PyErr_Format(PyExc_ValueError, "%s and %s must not overlap",
                     left_name, right_name);
        return -1;
    }
    return 0;
}

int validate_array_layout(PyArrayObject *array, int type, int dimensions,
                          const npy_intp *shape, int writable,
                          const char *name) {
    if (dimensions < 1 || dimensions > NPY_MAXDIMS ||
        PyArray_TYPE(array) != type || PyArray_NDIM(array) != dimensions) {
        PyErr_Format(PyExc_ValueError, "%s has an invalid dtype or dimensions", name);
        return -1;
    }
    for (int axis = 0; axis < dimensions; axis++) {
        if (shape != NULL && shape[axis] >= 0 &&
            PyArray_DIM(array, axis) != shape[axis]) {
            PyErr_Format(PyExc_ValueError, "%s has an invalid shape", name);
            return -1;
        }
    }
    if (writable ? !PyArray_ISCARRAY(array) : !PyArray_ISCARRAY_RO(array)) {
        PyErr_Format(PyExc_ValueError,
                     "%s must be %s, aligned, and C-contiguous", name,
                     writable ? "writable" : "read-only or writable");
        return -1;
    }
    if (writable && !PyArray_ISWRITEABLE(array)) {
        PyErr_Format(PyExc_ValueError, "%s must be writable", name);
        return -1;
    }
    return 0;
}

int validate_finite_prefix(PyArrayObject *array, npy_intp elements,
                           const char *name) {
    if (elements < 0 || elements > PyArray_SIZE(array)) {
        PyErr_Format(PyExc_ValueError, "%s element count is invalid", name);
        return -1;
    }
    const int type = PyArray_TYPE(array);
    if (type != NPY_DOUBLE && type != NPY_FLOAT32) {
        PyErr_Format(PyExc_TypeError, "%s must contain floating-point values", name);
        return -1;
    }
    for (npy_intp index = 0; index < elements; index++) {
        const double value = type == NPY_DOUBLE
            ? ((const double *)PyArray_DATA(array))[index]
            : (double)((const float *)PyArray_DATA(array))[index];
        if (!isfinite(value)) {
            PyErr_Format(PyExc_ValueError, "%s values must be finite", name);
            return -1;
        }
    }
    return 0;
}

int validate_array(PyArrayObject *array, int type, int dimensions,
                   const npy_intp *shape, int writable, double minimum,
                   double maximum, const char *name) {
    if (validate_array_layout(array, type, dimensions, shape, writable, name) < 0)
        return -1;
    if (type == NPY_DOUBLE || type == NPY_FLOAT32) {
        const npy_intp count = PyArray_SIZE(array);
        for (npy_intp index = 0; index < count; index++) {
            const double value = type == NPY_DOUBLE
                ? ((const double *)PyArray_DATA(array))[index]
                : (double)((const float *)PyArray_DATA(array))[index];
            if (!isfinite(value) || value < minimum || value > maximum) {
                PyErr_Format(PyExc_ValueError,
                             "%s values must be finite and in range", name);
                return -1;
            }
        }
    }
    return 0;
}

int validate_int16_array_range(PyArrayObject *array, int dimensions,
                               const npy_intp *shape, int writable,
                               int16_t minimum, int16_t maximum,
                               const char *name) {
    if (validate_array(array, NPY_INT16, dimensions, shape, writable,
                       -INFINITY, INFINITY, name) < 0)
        return -1;
    const npy_intp count = PyArray_SIZE(array);
    const int16_t *values = (const int16_t *)PyArray_DATA(array);
    for (npy_intp index = 0; index < count; index++) {
        if (values[index] < minimum || values[index] > maximum) {
            PyErr_Format(PyExc_ValueError, "%s values are outside the supported range", name);
            return -1;
        }
    }
    return 0;
}

int validate_fixed_array(PyArrayObject *array, int type, int dimensions,
                                npy_intp first, npy_intp second,
                                const char *name) {
    npy_intp shape[2] = {first, second};
    if (dimensions < 1 || dimensions > 2)
        return validate_array(array, type, dimensions, NULL, 0,
                              -INFINITY, INFINITY, name);
    return validate_array(array, type, dimensions, shape, 0,
                          -INFINITY, INFINITY, name);
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
