# lumistripe-cli

Headless GPIO runtime for Lumistripe on Raspberry Pi.

Run the DJ selector on a 42-pixel stripe with a PCM2902 audio device:

```bash
uv run lumistripe --pixels 42 --mode dj --audio-device 1 \
    --data-pin 15 --clock-pin 14 \
    --mic-profile pcm2902 \
    --debug-selector
```

For live mic tuning without GPIO, run audio debug mode:

```bash
lumistripe-cli --audio-debug --audio-device 2
```

Measure a microphone and print recommended tuning flags:

```bash
lumistripe-cli --calibrate-audio 3 --audio-device 2
```

Apply calibration before Dynamic/audio-debug runtime starts:

```bash
lumistripe-cli --mode dynamic --auto-calibrate-audio 3
lumistripe-cli --audio-debug --auto-calibrate-audio 3
```

Playback and audio source are separate. Static and Cycling default to no audio;
Dynamic defaults to the microphone:

```bash
lumistripe-cli --mode static --animation aurora
lumistripe-cli --mode cycling --cycle-order shuffle --cycle-timing fixed --cycle-interval 30
lumistripe-cli --mode dynamic --audio-source mic
```
