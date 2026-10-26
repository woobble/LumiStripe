#define LUMISTRIPE_RENDER_IMPORT_NUMPY
#include "native_common.h"

static struct PyModuleDef module_definition = {
    PyModuleDef_HEAD_INIT,
    .m_name = "lumistripe.render._native",
    .m_doc = "Portable scalar C kernels for RGBA frame processing",
    .m_size = -1,
};

PyMODINIT_FUNC PyInit__native(void) {
    import_array();
    if (PyErr_Occurred())
        return NULL;
    PyObject *module = PyModule_Create(&module_definition);
    if (module == NULL)
        return NULL;
    PyMethodDef *method_tables[] = {
        native_composite_methods(),
        native_patterns_methods(),
        native_particles_methods(),
        native_reactive_methods(),
    };
    for (size_t i = 0; i < sizeof(method_tables) / sizeof(method_tables[0]); i++) {
        if (PyModule_AddFunctions(module, method_tables[i]) < 0) {
            Py_DECREF(module);
            return NULL;
        }
    }
    return module;
}
