# lumistripe-core

`lumistripe` provides an in-memory `Stripe` controller for testing and
animation work, a hardware-backed `SPIStripe`, and the legacy `GPIOStripe`.

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
