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

CI builds the pure, audio, and all profiles on x86. A Raspberry Pi arm64
self-hosted smoke job is available when the repository variable
`LUMISTRIPE_PI_RUNNER_ENABLED` is set to `true`.
