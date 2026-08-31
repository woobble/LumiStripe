#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <numpy/arrayobject.h>
#include <stdint.h>
#include <string.h>

static int frame_size_for_pixels(npy_intp pixel_count, size_t *frame_size) {
    if (pixel_count < 0) {
        PyErr_SetString(PyExc_ValueError, "pixel count must not be negative");
        return -1;
    }
    if ((size_t)pixel_count > (SIZE_MAX - 57u) / 26u) {
        PyErr_SetString(PyExc_OverflowError, "encoded frame is too large");
        return -1;
    }
    *frame_size = (50u + (size_t)pixel_count * 26u + 7u) / 8u;
    return 0;
}

static inline void write_bit(uint8_t *destination, size_t bit_index, int value) {
    if (value)
        destination[bit_index >> 3] |= (uint8_t)(1u << (7u - (bit_index & 7u)));
}

static PyObject *sm16716_frame_size(PyObject *module, PyObject *args) {
    (void)module;
    Py_ssize_t pixel_count;
    size_t frame_size;
    if (!PyArg_ParseTuple(args, "n", &pixel_count))
        return NULL;
    if (frame_size_for_pixels((npy_intp)pixel_count, &frame_size) < 0)
        return NULL;
    return PyLong_FromSize_t(frame_size);
}

static PyObject *sm16716_encode_into(PyObject *module, PyObject *args) {
    (void)module;
    PyArrayObject *pixels;
    PyObject *destination_object;
    Py_buffer destination = {0};

    if (!PyArg_ParseTuple(args, "O!O", &PyArray_Type, &pixels, &destination_object))
        return NULL;
    if (PyArray_NDIM(pixels) != 2 || PyArray_DIM(pixels, 1) != 4) {
        PyErr_SetString(PyExc_ValueError, "pixels must be a 2-D array of shape (n, 4)");
        return NULL;
    }
    if (PyArray_TYPE(pixels) != NPY_UINT8) {
        PyErr_SetString(PyExc_ValueError, "pixels must have dtype uint8");
        return NULL;
    }
    if (!PyArray_ISCARRAY_RO(pixels)) {
        PyErr_SetString(PyExc_ValueError, "pixels must be C-contiguous");
        return NULL;
    }

    npy_intp pixel_count = PyArray_DIM(pixels, 0);
    size_t frame_size;
    if (frame_size_for_pixels(pixel_count, &frame_size) < 0)
        return NULL;
    if (PyObject_GetBuffer(
            destination_object,
            &destination,
            PyBUF_WRITABLE | PyBUF_C_CONTIGUOUS | PyBUF_FORMAT) < 0)
        return NULL;
    if (destination.itemsize != 1) {
        PyBuffer_Release(&destination);
        PyErr_SetString(PyExc_ValueError, "destination must contain one-byte elements");
        return NULL;
    }
    if ((size_t)destination.len < frame_size) {
        PyBuffer_Release(&destination);
        PyErr_Format(
            PyExc_ValueError,
            "destination is too small: need %zu bytes, got %zd",
            frame_size,
            destination.len
        );
        return NULL;
    }

    const uint8_t *source = (const uint8_t *)PyArray_DATA(pixels);
    uint8_t *output = (uint8_t *)destination.buf;
    memset(output, 0, frame_size);

    size_t bit_index = 50u;
    for (npy_intp pixel = 0; pixel < pixel_count; pixel++) {
        const uint8_t *rgba = source + (size_t)pixel * 4u;
        uint32_t alpha = rgba[3];
        write_bit(output, bit_index++, 1);
        for (int channel = 0; channel < 3; channel++) {
            uint8_t scaled = (uint8_t)((uint32_t)rgba[channel] * alpha / 255u);
            for (int bit = 7; bit >= 0; bit--)
                write_bit(output, bit_index++, (scaled >> bit) & 1);
        }
    }
    /* Trailing zero clocks and byte padding were established by memset. */

    PyBuffer_Release(&destination);
    Py_RETURN_NONE;
}

static PyMethodDef module_methods[] = {
    {"frame_size", sm16716_frame_size, METH_VARARGS,
     "Return the encoded SM16716 frame size for a pixel count."},
    {"encode_into", sm16716_encode_into, METH_VARARGS,
     "Encode an (n,4) uint8 RGBA array into a writable byte buffer."},
    {NULL, NULL, 0, NULL}
};

static struct PyModuleDef module_definition = {
    PyModuleDef_HEAD_INIT,
    .m_name = "lumistripe.gpio._sm16716",
    .m_doc = "Allocation-free SM16716 frame encoding helpers",
    .m_size = -1,
    .m_methods = module_methods,
};

PyMODINIT_FUNC PyInit__sm16716(void) {
    import_array();
    if (PyErr_Occurred())
        return NULL;
    return PyModule_Create(&module_definition);
}
