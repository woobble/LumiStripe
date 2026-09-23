# Native extension build profiles

`lumistripe-core` remains one distribution, but native extensions are opt-in
profiles. The default `LUMISTRIPE_BUILD_EXTENSIONS=none` build is pure Python;
`audio`, `gpio`, `spi`, and `all` select the corresponding extension set.
Optional runtime dependencies remain package extras, while the build profile
controls whether the C extension is compiled.

Portable compiler flags are used by default. `-march=native` is opt-in through
`LUMISTRIPE_NATIVE_OPTIMIZATION=1` for device-local builds and must not be used
for distributable wheels.

The source tree keeps a development symlink to the KISS FFT submodule. The
custom sdist command copies the required sources into the release tree, which
makes clean sdist builds independent of that symlink.

CI builds the pure, audio, and all profiles on x86. The repository also has a
persistent Raspberry Pi 5 arm64 runner with the custom GitHub Actions label
`rpi5`. The ARM64 job builds the `all` profile, runs the complete core test
suite, and verifies that the wheel contains native extensions and that the
source distribution contains the bundled KISS FFT sources.

The `rpi5` job runs only on pushes to the protected `main` branch and on
explicit `workflow_dispatch` runs. Pull requests remain on hosted runners so
code from forks cannot execute on the persistent home/device runner.

The runner must be pre-provisioned with the GitHub Actions service, Linux
ARM64/aarch64, Python 3.12, `uv`, Git submodule support, a C compiler, `make`,
and the native development libraries required by the `all` profile. CI checks
these prerequisites but does not upgrade the runner's operating-system
packages on every run.
