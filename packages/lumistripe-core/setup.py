from setuptools import setup, Extension
import numpy

ext_gpio = Extension(
    "lumistripe._gpiomem",
    sources=["src/lumistripe/_gpiomem.c"],
    include_dirs=[numpy.get_include()],
    extra_compile_args=["-std=c11", "-Wall", "-Wextra", "-Wpedantic", "-O2"],
)

ext_audio = Extension(
    "lumistripe._audio",
    sources=[
        "src/lumistripe/_audio.c",
        "src/lumistripe/kissfft/kiss_fft.c",
        "src/lumistripe/kissfft/kiss_fftr.c",
    ],
    include_dirs=[numpy.get_include(), "src/lumistripe/kissfft"],
    libraries=["m"],
    extra_compile_args=[
        "-std=c11", "-Wall", "-Wextra", "-Wpedantic",
        "-O3", "-march=native", "-flto",
        "-ffinite-math-only", "-fno-math-errno",
    ],
    extra_link_args=["-flto", "-O3"],
)

setup(ext_modules=[ext_gpio, ext_audio])
