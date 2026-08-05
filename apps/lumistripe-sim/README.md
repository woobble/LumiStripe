# lumistripe-sim

Tkinter simulator for LumiStripe animations, demo audio, and live microphone input.

Run the simulator:

```bash
lumistripe-sim
```

Start directly in Dynamic mode with automatic input calibration:

```bash
lumistripe-sim --mode dynamic --auto-calibrate-audio 3
```

Keyboard shortcuts:
- `Left` / `Right` - Previous / next animation
- `s` - Static mode
- `c` - Cycling mode
- `d` - Dynamic mode
- `k` - Calibrate microphone levels
- `Escape` - Quit

Use synthetic audio without adding another playback mode:

```bash
lumistripe-sim --mode dynamic --audio-source demo
```
