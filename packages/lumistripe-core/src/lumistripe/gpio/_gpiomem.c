#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <numpy/arrayobject.h>
#include <stdint.h>
#include <fcntl.h>
#include <sys/mman.h>
#include <unistd.h>
#include <time.h>
#include <errno.h>
#include <string.h>
#include <stdlib.h>

#define GPIO_MEM_PATH "/dev/gpiomem"

/* BCM2835/2711/2712 GPIO register offsets (32-bit word offsets) */
#define GPSET0  0x1C
#define GPSET1  0x20
#define GPCLR0  0x28
#define GPCLR1  0x2C

/* Number of bytes to mmap (one page covers all GPIO registers) */
#define GPIO_MAP_BYTES 4096

/* Calibration loop iterations — enough for stable measurement on 1-2 GHz */
#define CALIBRATE_ITERATIONS 2000000

/* Default ns-per-iter if clock_gettime fails (safe for ~1.5 GHz) */
#define DEFAULT_NS_PER_ITER 6.0

typedef struct {
    PyObject_HEAD
    volatile uint32_t *gpio_mem;
    int data_pin;
    int clock_pin;
    int fd;
    double ns_per_iter;
    int flushing;
} GPIOMem;

static void gpio_set(volatile uint32_t *gpio, int pin, int value) {
    if (value) {
        if (pin < 32)
            gpio[GPSET0 / 4] = (uint32_t)(1u << pin);
        else
            gpio[GPSET1 / 4] = (uint32_t)(1u << (pin - 32));
    } else {
        if (pin < 32)
            gpio[GPCLR0 / 4] = (uint32_t)(1u << pin);
        else
            gpio[GPCLR1 / 4] = (uint32_t)(1u << (pin - 32));
    }
}

static void gpio_set_fsel(volatile uint32_t *gpio, int pin, int mode) {
    int reg_idx = (pin / 10);
    int shift = (pin % 10) * 3;
    uint32_t mask = ~(7u << shift);
    uint32_t val = ((uint32_t)mode & 7u) << shift;
    gpio[reg_idx] = (gpio[reg_idx] & mask) | val;
}

static void busy_wait_ns(GPIOMem *self, long ns) {
    if (ns <= 0) return;
    volatile long i;
    long count = (long)((double)ns / self->ns_per_iter + 0.5);
    if (count < 1) count = 1;
    for (i = 0; i < count; i++) {}
}

static void pulse(GPIOMem *self, int data) {
    gpio_set(self->gpio_mem, self->data_pin, data);
    busy_wait_ns(self, 100);
    gpio_set(self->gpio_mem, self->clock_pin, 1);
    busy_wait_ns(self, 500);
    gpio_set(self->gpio_mem, self->clock_pin, 0);
    busy_wait_ns(self, 500);
}

static void flush_rgb_frame(GPIOMem *self, const uint8_t *rgb, npy_intp num_pixels) {
    for (int i = 0; i < 50; i++)
        pulse(self, 0);

    for (npy_intp i = 0; i < num_pixels; i++) {
        uint8_t sr = rgb[i * 3 + 0];
        uint8_t sg = rgb[i * 3 + 1];
        uint8_t sb = rgb[i * 3 + 2];

        pulse(self, 1);

        for (int bit = 7; bit >= 0; bit--)
            pulse(self, (sr >> bit) & 1);
        for (int bit = 7; bit >= 0; bit--)
            pulse(self, (sg >> bit) & 1);
        for (int bit = 7; bit >= 0; bit--)
            pulse(self, (sb >> bit) & 1);
    }

    for (npy_intp i = 0; i < num_pixels; i++)
        pulse(self, 0);
}

static int GPIOMem_init(GPIOMem *self, PyObject *args, PyObject *kwds) {
    static char *kwlist[] = {"data_pin", "clock_pin", NULL};
    int data_pin, clock_pin;
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "ii", kwlist, &data_pin, &clock_pin))
        return -1;

    self->fd = -1;
    self->gpio_mem = NULL;
    self->data_pin = data_pin;
    self->clock_pin = clock_pin;
    self->flushing = 0;

    if (data_pin < 0 || data_pin > 53 || clock_pin < 0 || clock_pin > 53) {
        PyErr_SetString(PyExc_ValueError, "GPIO pin must be 0-53");
        return -1;
    }

    self->fd = open(GPIO_MEM_PATH, O_RDWR);
    if (self->fd < 0) {
        PyErr_Format(PyExc_OSError, "cannot open %s: %s",
                     GPIO_MEM_PATH, strerror(errno));
        return -1;
    }

    self->gpio_mem = (volatile uint32_t *)mmap(
        NULL, GPIO_MAP_BYTES, PROT_READ | PROT_WRITE,
        MAP_SHARED, self->fd, 0);
    if (self->gpio_mem == MAP_FAILED) {
        PyErr_Format(PyExc_OSError, "mmap failed: %s", strerror(errno));
        close(self->fd);
        self->fd = -1;
        return -1;
    }

    gpio_set_fsel(self->gpio_mem, data_pin, 1);
    gpio_set_fsel(self->gpio_mem, clock_pin, 1);

    self->ns_per_iter = DEFAULT_NS_PER_ITER;
    struct timespec t0, t1;
    if (clock_gettime(CLOCK_MONOTONIC, &t0) == 0) {
        volatile long _cal_iters = CALIBRATE_ITERATIONS;
        volatile long _cal_i;
        for (_cal_i = 0; _cal_i < _cal_iters; _cal_i++) {}
        if (clock_gettime(CLOCK_MONOTONIC, &t1) == 0) {
            double elapsed_ns = (double)(t1.tv_sec - t0.tv_sec) * 1e9
                              + (double)(t1.tv_nsec - t0.tv_nsec);
            if (elapsed_ns > 0.0)
                self->ns_per_iter = elapsed_ns / (double)CALIBRATE_ITERATIONS;
        }
    }

    return 0;
}

static void GPIOMem_dealloc(GPIOMem *self) {
    if (self->gpio_mem != NULL) {
        munmap((void *)self->gpio_mem, GPIO_MAP_BYTES);
        self->gpio_mem = NULL;
    }
    if (self->fd >= 0) {
        close(self->fd);
        self->fd = -1;
    }
    Py_TYPE(self)->tp_free((PyObject *)self);
}

static PyObject *GPIOMem_set_values(GPIOMem *self, PyObject *args) {
    int data, clock;
    if (!PyArg_ParseTuple(args, "pp", &data, &clock))
        return NULL;
    if (self->gpio_mem == NULL) {
        PyErr_SetString(PyExc_RuntimeError, "GPIOMem is closed");
        return NULL;
    }
    gpio_set(self->gpio_mem, self->data_pin, data);
    gpio_set(self->gpio_mem, self->clock_pin, clock);
    Py_RETURN_NONE;
}

static PyObject *GPIOMem_close(GPIOMem *self, PyObject *args) {
    (void)args;
    if (self->flushing) {
        PyErr_SetString(PyExc_RuntimeError, "cannot close GPIOMem while flush is active");
        return NULL;
    }
    if (self->gpio_mem != NULL) {
        munmap((void *)self->gpio_mem, GPIO_MAP_BYTES);
        self->gpio_mem = NULL;
    }
    if (self->fd >= 0) {
        close(self->fd);
        self->fd = -1;
    }
    Py_RETURN_NONE;
}

static PyObject *GPIOMem_flush(GPIOMem *self, PyObject *args) {
    PyArrayObject *pixels;
    if (!PyArg_ParseTuple(args, "O!", &PyArray_Type, &pixels))
        return NULL;

    if (PyArray_NDIM(pixels) != 2 || PyArray_DIM(pixels, 1) != 4) {
        PyErr_SetString(PyExc_ValueError,
                        "pixels must be a 2-D array of shape (n, 4)");
        return NULL;
    }
    if (PyArray_TYPE(pixels) != NPY_UINT8) {
        PyErr_SetString(PyExc_ValueError,
                        "pixels must have dtype uint8");
        return NULL;
    }
    if (self->gpio_mem == NULL) {
        PyErr_SetString(PyExc_RuntimeError, "GPIOMem is closed");
        return NULL;
    }

    npy_intp num_pixels = PyArray_DIM(pixels, 0);
    uint8_t *data = (uint8_t *)PyArray_DATA(pixels);
    uint8_t *rgb = NULL;

    if (num_pixels > 0) {
        if (num_pixels > PY_SSIZE_T_MAX / 3) {
            PyErr_SetString(PyExc_MemoryError, "pixel buffer is too large");
            return NULL;
        }
        rgb = (uint8_t *)malloc((size_t)num_pixels * 3u);
        if (rgb == NULL) {
            PyErr_NoMemory();
            return NULL;
        }
    }

    for (npy_intp i = 0; i < num_pixels; i++) {
        uint32_t r = data[i * 4 + 0];
        uint32_t g = data[i * 4 + 1];
        uint32_t b = data[i * 4 + 2];
        uint32_t a = data[i * 4 + 3];

        rgb[i * 3 + 0] = (uint8_t)(r * a / 255);
        rgb[i * 3 + 1] = (uint8_t)(g * a / 255);
        rgb[i * 3 + 2] = (uint8_t)(b * a / 255);
    }

    self->flushing = 1;
    Py_BEGIN_ALLOW_THREADS
    flush_rgb_frame(self, rgb, num_pixels);
    Py_END_ALLOW_THREADS
    self->flushing = 0;

    free(rgb);

    Py_RETURN_NONE;
}

static PyMethodDef GPIOMem_methods[] = {
    {"set_values", (PyCFunction)GPIOMem_set_values, METH_VARARGS,
     "Set data and clock pin logic levels."},
    {"flush",      (PyCFunction)GPIOMem_flush,      METH_VARARGS,
     "Flush an (n,4) uint8 RGBA pixel buffer to WS2801 LEDs."},
    {"close",      (PyCFunction)GPIOMem_close,      METH_NOARGS,
     "Release the /dev/gpiomem mapping and file descriptor."},
    {NULL, NULL, 0, NULL}
};

static PyTypeObject GPIOMemType = {
    PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name        = "lumistripe.gpio._gpiomem.GPIOMem",
    .tp_doc         = "Raspberry Pi /dev/gpiomem GPIO fast writer",
    .tp_basicsize   = sizeof(GPIOMem),
    .tp_itemsize    = 0,
    .tp_flags       = Py_TPFLAGS_DEFAULT,
    .tp_init        = (initproc)GPIOMem_init,
    .tp_dealloc     = (destructor)GPIOMem_dealloc,
    .tp_methods     = GPIOMem_methods,
    .tp_new         = PyType_GenericNew,
};

static struct PyModuleDef moduledef = {
    PyModuleDef_HEAD_INIT,
    .m_name = "lumistripe.gpio._gpiomem",
    .m_doc  = "GPIO bit-banging via /dev/gpiomem direct register access",
    .m_size = -1,
};

PyMODINIT_FUNC PyInit__gpiomem(void) {
    import_array();
    if (PyErr_Occurred())
        return NULL;

    if (PyType_Ready(&GPIOMemType) < 0)
        return NULL;

    PyObject *m = PyModule_Create(&moduledef);
    if (m == NULL)
        return NULL;

    Py_INCREF(&GPIOMemType);
    if (PyModule_AddObject(m, "GPIOMem", (PyObject *)&GPIOMemType) < 0) {
        Py_DECREF(&GPIOMemType);
        Py_DECREF(m);
        return NULL;
    }

    return m;
}
