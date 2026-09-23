# lumistripe-core

`lumistripe` provides an in-memory `Stripe` controller for testing and
animation work, a hardware-backed `SPIStripe`, and the legacy `GPIOStripe`.

The default distribution is pure Python. Native audio, GPIO, and SPI
extensions are opt-in build profiles so a wheel built on a workstation does
not accidentally contain machine-specific hardware code:

```bash
# portable wheel (the default)
uv build --package lumistripe-core

# explicit native profiles
LUMISTRIPE_BUILD_EXTENSIONS=audio uv build --package lumistripe-core
LUMISTRIPE_BUILD_EXTENSIONS=gpio uv build --package lumistripe-core
LUMISTRIPE_BUILD_EXTENSIONS=spi uv build --package lumistripe-core
LUMISTRIPE_BUILD_EXTENSIONS=all uv build --package lumistripe-core
```

Portable compiler flags are used by default. Set
`LUMISTRIPE_NATIVE_OPTIMIZATION=1` only for a device-local build; it enables
`-march=native` and must not be used for a distributable wheel. Source
distributions materialize the KISS FFT submodule, so the development symlink
is not required when building from a clean sdist.

Install hardware SPI support with:

```bash
pip install lumistripe-core[spi]
```

```python
from lumistripe import SPIConfig, SPIStripe

stripe = SPIStripe(SPIConfig(device="/dev/spidev0.0", speed_hz=1_000_000), 80)
```

Install the legacy userspace GPIO support with:

```bash
pip install lumistripe-core[gpio]
```

Example:

```python
from lumistripe import Config, GPIOStripe

stripe = GPIOStripe(Config(gpio_data=14, gpio_clock=15), 80)
```

Use `PlaybackEngine` for the shared Static, Cycling, and Dynamic behavior used by
the command-line runtime and simulator. Audio snapshots are optional in Static
and Cycling and required for meaningful Dynamic selection.
