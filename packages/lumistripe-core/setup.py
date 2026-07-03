from setuptools import setup, Extension
import numpy

ext = Extension(
    "lumistripe._gpiomem",
    sources=["src/lumistripe/_gpiomem.c"],
    include_dirs=[numpy.get_include()],
)

setup(ext_modules=[ext])
