import os

import numpy
from setuptools import Extension, setup

requested_extensions = {
    item.strip().lower()
    for item in os.environ.get("LUMISTRIPE_BUILD_EXTENSIONS", "all").split(",")
    if item.strip()
}
build_all_extensions = "all" in requested_extensions
optimized_native_build = os.environ.get("LUMISTRIPE_NATIVE_OPTIMIZATION", "0") == "1"


def optimization_flags(level: str) -> list[str]:
    flags = [level, "-flto", "-ffinite-math-only", "-fno-math-errno"]
    if optimized_native_build:
        flags.append("-march=native")
    return flags

ext_gpio = Extension(
    "lumistripe.gpio._gpiomem",
    sources=["src/lumistripe/gpio/_gpiomem.c"],
    include_dirs=[numpy.get_include()],
    extra_compile_args=["-std=c11", "-Wall", "-Wextra", "-Wpedantic", "-O2"],
)

ext_sm16716 = Extension(
    "lumistripe.gpio._sm16716",
    sources=["src/lumistripe/gpio/_sm16716.c"],
    include_dirs=[numpy.get_include()],
    extra_compile_args=[
        "-std=c11", "-Wall", "-Wextra", "-Wpedantic",
        *optimization_flags("-O3"),
    ],
    extra_link_args=["-flto", "-O3"],
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
        *optimization_flags("-O3"),
    ],
    extra_link_args=["-flto", "-O3"],
)

extensions = []
if build_all_extensions or "gpio" in requested_extensions:
    extensions.append(ext_gpio)
if build_all_extensions or "spi" in requested_extensions:
    extensions.append(ext_sm16716)
if build_all_extensions or "audio" in requested_extensions:
    extensions.append(ext_audio)

setup(ext_modules=extensions)
