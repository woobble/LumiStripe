# Native extension build profiles

Native core extensions are built by default for full installations, but the
build accepts `LUMISTRIPE_BUILD_EXTENSIONS` with comma-separated values
`audio`, `gpio`, and `spi`, or `none` for a pure-Python package build.

Portable compiler flags are the default. `-march=native` is opt-in through
`LUMISTRIPE_NATIVE_OPTIMIZATION=1` and must not be used for distributable
wheels.
