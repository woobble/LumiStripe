#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <numpy/arrayobject.h>
#include <math.h>
#include <stdint.h>

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

static PyMethodDef module_methods[] = {
    {"correct_rgba_into", correct_rgba_into, METH_VARARGS,
     "Apply integer RGB correction into a contiguous RGBA destination."},
    {"apply_alpha_scale_into", apply_alpha_scale_into, METH_VARARGS,
     "Copy RGBA pixels and floor-scale alpha into reusable destinations."},
    {"composite_screen_rgba_into", composite_screen_rgba_into, METH_VARARGS,
     "Composite up to two alpha-weighted RGBA overlays using SCREEN blending."},
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
