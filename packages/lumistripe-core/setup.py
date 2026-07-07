from setuptools import setup, Extension
import numpy

ext_gpio = Extension(
    "lumistripe.gpio._gpiomem",
    sources=["src/lumistripe/gpio/_gpiomem.c"],
    include_dirs=[numpy.get_include()],
    extra_compile_args=["-std=c11", "-Wall", "-Wextra", "-Wpedantic", "-O2"],
)

ext_audio = Extension(
    "lumistripe.audio._audio",
    sources=[
        "src/lumistripe/audio/_audio.c",
        "src/lumistripe/audio/kissfft/kiss_fft.c",
        "src/lumistripe/audio/kissfft/kiss_fftr.c",
    ],
    include_dirs=[numpy.get_include(), "src/lumistripe/audio/kissfft"],
    libraries=["m"],
    extra_compile_args=[
        "-std=c11", "-Wall", "-Wextra", "-Wpedantic",
        "-O3", "-march=native", "-flto",
        "-ffinite-math-only", "-fno-math-errno",
    ],
    extra_link_args=["-flto", "-O3"],
)

setup(ext_modules=[ext_gpio, ext_audio])
