from setuptools import setup, Extension
import numpy

ext_gpio = Extension(
    "lumistripe._gpiomem",
    sources=["src/lumistripe/_gpiomem.c"],
    include_dirs=[numpy.get_include()],
)

ext_audio = Extension(
    "lumistripe._audio",
    sources=[
        "src/lumistripe/_audio.c",
        "src/lumistripe/kissfft/kiss_fft.c",
    ],
    include_dirs=[numpy.get_include(), "src/lumistripe/kissfft"],
    libraries=["m"],
)

setup(ext_modules=[ext_gpio, ext_audio])
