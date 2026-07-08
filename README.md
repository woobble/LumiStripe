# LumiStripe

[![codecov](https://codecov.io/github/woobble/LumiStripe/graph/badge.svg?token=2ZJDBFMOOW)](https://codecov.io/github/woobble/LumiStripe)

**Python-powered LED animation engine with audio-reactive lighting.**

LumiStripe drives 2-wire addressable LED strips from a Raspberry Pi or other Linux single-board computer via GPIO bit-banging, and provides a rich animation engine with **38+ built-in effects**, real-time **FFT audio analysis**, **music-driven animation selection**, and a cross-platform **Tkinter GUI simulator**.

## Features

- **GPIO Driver** — Bit-bangs a 2-wire protocol on any two GPIO lines using `libgpiod` (v2.x)
- **38+ Animations** — Fire, Rainbow, Confetti, Aurora, LightningStrike, PlasmaRave, and many more, with `tick` and `tick_audio` modes
- **Audio Reactive** — Real-time FFT analysis with 8 frequency bands, beat detection, BPM estimation, and onset/transient detection
- **Music Classifier** — Automatically selects animations based on 8 music moods (Ambient, Groovy, Bass Heavy, Chaotic, etc.)
- **Simulator** — Tkinter GUI with MANUAL (browse animations), DEMO (synthetic beat), and MIC (live audio input) modes
- **CLI** — Launch the simulator from the terminal with `lumistripe`

## Quick Start

```bash
pip install lumistripe-core
```

Use the in-memory `Stripe` for testing without hardware:

```python
from lumistripe import AnimationPlayer, Stripe

stripe = Stripe(80)
player = AnimationPlayer.party()
player.step(stripe)  # renders one frame
```

## Hardware Setup

Connect 2-wire (clock + data) addressable LED strips to GPIO pins on a Raspberry Pi and install with GPIO support:

```bash
pip install lumistripe-core[gpio]
```

```python
from lumistripe import Config, GPIOStripe

stripe = GPIOStripe(Config(gpio_data=14, gpio_clock=15), 80)
```

## Audio Setup

For audio-reactive animations, install with audio support:

```bash
pip install lumistripe-core[audio]
```

LumiStripe works with any microphone or line-in device supported by `sounddevice`.

GPIO and audio can be combined for audio-reactive lighting on real hardware:

```bash
pip install lumistripe-core[gpio,audio]
```

```python
from lumistripe import (AnimationPlayer, AudioInput, Config,
                         GPIOStripe, MusicDrivenSelector)

stripe = GPIOStripe(Config(gpio_data=14, gpio_clock=15), 80)
player = AnimationPlayer.party()
selector = MusicDrivenSelector()
selector.set_auto_select(True)

with AudioInput.new() as audio:
    while True:
        features = audio.read_features()
        selector.update(player, features)  # picks animation matching the music
        player.step(stripe)                # renders one frame to the LEDs
```

## Animations

All 38+ animations can be browsed in the simulator. Each animation is tagged with one or more mood classes (e.g., `FAST_PARTY`, `BASS_HEAVY`, `CHAOTIC`, `GROOVY`, `AMBIENT`). The `MusicDrivenSelector` automatically picks the best-matching animation based on live audio analysis.

## Simulator

Launch the Tkinter GUI simulator:

```bash
lumistripe
```

Keyboard shortcuts:
- `←` / `→` — Previous / next animation
- `m` — MANUAL mode (browse animations)
- `d` — DEMO mode (synthetic beat)
- `a` — MIC mode (live microphone)
- `s` — Toggle auto-select
- `c` — Calibrate microphone levels
- `Escape` — Quit

## Audio Calibration

Measure the selected microphone and print recommended tuning flags:

```bash
lumistripe --calibrate-audio 3 --audio-device usb
```

Apply calibration automatically before starting MIC mode:

```bash
lumistripe --mode mic --auto-calibrate-audio 3
lumistripe --audio-debug --auto-calibrate-audio 3
lumistripe-sim --mode mic --auto-calibrate-audio 3
```

## Development

```bash
# Setup
uv sync

# Run tests
uv run python -m pytest -q

# Run tests with coverage report
uv run python -m pytest -q --cov-report=term-missing --cov-report=xml

# Lint and type-check
uv run ruff check .
uv run mypy packages/lumistripe-core/src/lumistripe apps/lumistripe-cli/src/lumistripe_cli apps/lumistripe-sim/src/lumistripe_sim
```
