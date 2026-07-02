# lumistripe-core

`lumistripe` provides an in-memory `Stripe` controller for testing and animation work, plus a hardware-backed `GPIOStripe` for Raspberry Pi / Linux GPIO output.

Install GPIO support with:

```bash
pip install lumistripe-core[gpio]
```

Example:

```python
from lumistripe import Config, GPIOStripe

stripe = GPIOStripe(Config(gpio_data=14, gpio_clock=15), 80)
```
