import os
import shutil
from pathlib import Path

import numpy
from setuptools import Extension, setup
from setuptools.command.sdist import sdist as _sdist

ROOT = Path(__file__).resolve().parent
KISSFFT_SOURCE = ROOT.parent.parent / "third_party" / "kissfft"
KISSFFT_PACKAGE = ROOT / "src" / "lumistripe" / "audio" / "kissfft"

requested_extensions = {
    item.strip().lower()
    for item in os.environ.get("LUMISTRIPE_BUILD_EXTENSIONS", "none").split(",")
    if item.strip()
}
valid_extensions = {"none", "audio", "gpio", "spi", "all"}
unknown_extensions = requested_extensions - valid_extensions
if unknown_extensions:
    raise RuntimeError(
        "unsupported LUMISTRIPE_BUILD_EXTENSIONS value(s): "
        + ", ".join(sorted(unknown_extensions))
    )
if "none" in requested_extensions and len(requested_extensions) > 1:
    raise RuntimeError("LUMISTRIPE_BUILD_EXTENSIONS=none cannot be combined with other profiles")
build_all_extensions = "all" in requested_extensions
optimized_native_build = os.environ.get("LUMISTRIPE_NATIVE_OPTIMIZATION", "0") == "1"


def optimization_flags() -> tuple[list[str], list[str]]:
    """Return portable compile/link flags unless optimization is explicit."""

    compile_flags = ["-std=c11", "-Wall", "-Wextra", "-Wpedantic", "-O2"]
    link_flags: list[str] = []
    if optimized_native_build:
        compile_flags.extend(
            ["-O3", "-flto", "-ffinite-math-only", "-fno-math-errno", "-march=native"]
        )
        link_flags.extend(["-flto", "-O3"])
    return compile_flags, link_flags


native_compile_flags, native_link_flags = optimization_flags()

ext_gpio = Extension(
    "lumistripe.gpio._gpiomem",
    sources=["src/lumistripe/gpio/_gpiomem.c"],
    include_dirs=[numpy.get_include()],
    extra_compile_args=native_compile_flags,
)

ext_sm16716 = Extension(
    "lumistripe.gpio._sm16716",
    sources=["src/lumistripe/gpio/_sm16716.c"],
    include_dirs=[numpy.get_include()],
    extra_compile_args=native_compile_flags,
    extra_link_args=native_link_flags,
)

kissfft_root = KISSFFT_PACKAGE if (KISSFFT_PACKAGE / "kiss_fft.c").exists() else KISSFFT_SOURCE


def package_path(path: Path) -> str:
    """Return a setup.py path relative to the package root."""

    return Path(os.path.relpath(path, ROOT)).as_posix()

ext_audio = Extension(
    "lumistripe.audio._audio",
    sources=[
        "src/lumistripe/audio/_audio.c",
        package_path(kissfft_root / "kiss_fft.c"),
        package_path(kissfft_root / "kiss_fftr.c"),
    ],
    include_dirs=[numpy.get_include(), package_path(kissfft_root)],
    libraries=["m"],
    extra_compile_args=native_compile_flags,
    extra_link_args=native_link_flags,
)

extensions = []
if build_all_extensions or "gpio" in requested_extensions:
    extensions.append(ext_gpio)
if build_all_extensions or "spi" in requested_extensions:
    extensions.append(ext_sm16716)
if build_all_extensions or "audio" in requested_extensions:
    extensions.append(ext_audio)


class SourceDistribution(_sdist):
    """Materialize the development KISS FFT link in release trees."""

    def make_release_tree(self, base_dir: str, files: list[str]) -> None:
        super().make_release_tree(base_dir, files)
        target = Path(base_dir) / "src" / "lumistripe" / "audio" / "kissfft"
        if target.is_symlink():
            target.unlink()
        if not target.exists():
            target.mkdir(parents=True)
        source = KISSFFT_SOURCE if KISSFFT_SOURCE.exists() else KISSFFT_PACKAGE
        if not source.exists():
            raise RuntimeError("KISS FFT submodule is required to build a source distribution")
        for filename in ("kiss_fft.c", "kiss_fft.h", "kiss_fftr.c", "kiss_fftr.h", "_kiss_fft_guts.h"):
            shutil.copy2(source / filename, target / filename)


setup(cmdclass={"sdist": SourceDistribution}, ext_modules=extensions)
