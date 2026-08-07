# LumiStripe

[![codecov](https://codecov.io/github/woobble/LumiStripe/graph/badge.svg?token=2ZJDBFMOOW)](https://codecov.io/github/woobble/LumiStripe)

**Python-powered LED animation engine with audio-reactive lighting.**

LumiStripe drives 2-wire addressable LED strips from a Raspberry Pi or other Linux single-board computer via GPIO bit-banging, and provides a rich animation engine with **38+ built-in effects**, real-time **FFT audio analysis**, **music-driven animation selection**, and a cross-platform **Tkinter GUI simulator**.

## Features

- **GPIO Driver** — Bit-bangs a 2-wire protocol on any two GPIO lines using `libgpiod` (v2.x)
- **38+ Animations** — Fire, Rainbow, Confetti, Aurora, LightningStrike, PlasmaRave, and many more, with `tick` and `tick_audio` modes
- **Audio Reactive** — Real-time FFT analysis with 8 frequency bands, beat detection, BPM estimation, and onset/transient detection
- **Layered Dynamic Selection** — Selects a smooth base animation and independently schedules up to two compatible beat/drop effects from live music features
- **Three Playback Modes** — Static, Cycling, and music-driven Dynamic playback in both the CLI and simulator
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

For stable continuously animated output, connect a 2-wire strip to Raspberry
Pi hardware SPI and install SPI support:

```bash
pip install lumistripe-core[spi]
```

```python
from lumistripe import SPIConfig, SPIStripe

stripe = SPIStripe(SPIConfig(device="/dev/spidev0.0"), 80)
```

On Raspberry Pi 4, SPI0 data is GPIO10 (physical pin 19) and clock is GPIO11
(physical pin 23). The legacy bit-banged backend remains available through
`GPIOStripe` and `lumistripe-core[gpio]`.

## Audio Setup

For audio-reactive animations, install with audio support:

```bash
pip install lumistripe-core[audio]
```

LumiStripe works with any microphone or line-in device supported by `sounddevice`.

SPI and audio can be combined for audio-reactive lighting on real hardware:

```bash
pip install lumistripe-core[spi,audio]
```

```python
from lumistripe import (AnimationPlayer, AudioInput, AudioSnapshot, SPIConfig,
                        SPIStripe, PlaybackConfig, PlaybackEngine,
                        PlaybackMode)

stripe = SPIStripe(SPIConfig(), 80)
player = AnimationPlayer.party()
playback = PlaybackEngine(player, PlaybackConfig(mode=PlaybackMode.DYNAMIC))

with AudioInput.new() as audio:
    while True:
        frame = audio.read()
        snapshot = AudioSnapshot.from_parts(frame, audio.read_features())
        playback.step(stripe, snapshot=snapshot)
```

## Animations

All animations can be browsed in Static mode. Animation metadata describes suitable energy, BPM, spectrum, mood, beat/drop support, and whether an effect is safe for Dynamic's calm state.

Dynamic mode composes one long-running base with at most one rhythmic and one
accent layer. Automatic base changes crossfade, while manual and Cycling modes
keep every standalone animation available. Strobe, Rainbow Strobe, and Police
remain excluded from Dynamic selection.

## Simulator

Launch the Tkinter GUI simulator:

```bash
lumistripe
```

Keyboard shortcuts:
- `←` / `→` — Previous / next animation
- `s` — Static mode
- `c` — Cycling mode
- `d` — Dynamic mode
- `k` — Calibrate microphone levels
- `Escape` — Quit

Audio source is configured independently from playback mode:

```bash
lumistripe-sim --mode cycling --audio-source demo
lumistripe-sim --mode static --audio-source mic
lumistripe-sim --mode dynamic                    # microphone by default
```

## Audio Calibration

Measure the selected microphone and print recommended tuning flags:

```bash
lumistripe --calibrate-audio 3 --audio-device usb
```

Apply calibration automatically before starting Dynamic mode:

```bash
lumistripe --mode dynamic --auto-calibrate-audio 3
lumistripe --audio-debug --auto-calibrate-audio 3
lumistripe-sim --mode dynamic --auto-calibrate-audio 3
```

For live base-animation, effect-layer, selector, and scheduler diagnostics:

```bash
lumistripe --mode dynamic --debug-selector
lumistripe --audio-debug --audio-debug-verbose
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
