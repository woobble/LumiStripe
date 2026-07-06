# lumistripe-cli

Headless GPIO runtime for Lumistripe on Raspberry Pi.

For live mic tuning without GPIO, run audio debug mode:

```bash
lumistripe-cli --audio-debug --audio-device 2
```

Measure a microphone and print recommended tuning flags:

```bash
lumistripe-cli --calibrate-audio 3 --audio-device 2
```

Apply calibration before MIC/audio-debug runtime starts:

```bash
lumistripe-cli --mode mic --auto-calibrate-audio 3
lumistripe-cli --audio-debug --auto-calibrate-audio 3
```
